"""
语义检索引擎插件（Phase 3 可选，不违反 Everything 铁律）

定位：
- 独立插件，默认不安装、不加载
- 不导入 core/index.py，核心代码不引用此模块
- BM25 默认（纯 Python，零安装）
- FAISS 可选（需要额外安装 faiss-cpu + sentence-transformers）

使用方式：
    engine = SemanticEngine(backend='bm25')
    engine.build([(rule_id, content), ...])
    results = engine.search("query text")
"""

from typing import List, Tuple


class SemanticEngine:
    """
    语义检索引擎插件。

    后端：
    - bm25: 纯 Python，零安装，对短文本效果好（默认）
    - faiss: 需要 numpy + faiss-cpu，高精度（可选）
    """

    def __init__(self, backend: str = 'bm25'):
        self.backend = backend
        self._index = None
        self._ids: List[str] = []
        self._encoder = None  # 仅 FAISS 后端使用

    def build(self, corpus: List[Tuple[str, str]]):
        """
        构建语义索引。

        Args:
            corpus: [(rule_id, content), ...]
        """
        self._ids = [rid for rid, _ in corpus]
        texts = [text for _, text in corpus]

        if self.backend == 'bm25':
            self._build_bm25(texts)
        elif self.backend == 'faiss':
            self._build_faiss(texts)

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        """中英文分词：优先 jieba，回退到字符级。"""
        try:
            import jieba
            return jieba.lcut(text.lower())
        except ImportError:
            tokens = []
            buf = []
            for ch in text.lower():
                if '\u4e00' <= ch <= '\u9fff':  # CJK 统一表意文字
                    if buf:
                        tokens.extend(''.join(buf).split())
                        buf = []
                    tokens.append(ch)
                else:
                    buf.append(ch)
            if buf:
                tokens.extend(''.join(buf).split())
            return tokens

    def _build_bm25(self, texts: List[str]):
        try:
            from rank_bm25 import BM25Okapi
            tokenized = [self._tokenize(t) for t in texts]
            self._index = BM25Okapi(tokenized)
        except ImportError:
            raise ImportError(
                "BM25 需要安装 rank-bm25: pip install rank-bm25"
            )

    def _build_faiss(self, texts: List[str]):
        try:
            from sentence_transformers import SentenceTransformer
            import faiss
            import numpy as np

            self._encoder = SentenceTransformer('all-MiniLM-L6-v2')
            embeddings = self._encoder.encode(
                texts, normalize_embeddings=True
            ).astype('float32')

            dim = embeddings.shape[1]
            self._index = faiss.IndexFlatIP(dim)
            self._index.add(embeddings)
        except ImportError:
            raise ImportError(
                "FAISS 后端需要安装: "
                "pip install faiss-cpu sentence-transformers"
            )

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """语义搜索。"""
        if self._index is None:
            return []

        if self.backend == 'bm25':
            tokenized = self._tokenize(query)
            scores = self._index.get_scores(tokenized)
            top = sorted(
                enumerate(scores), key=lambda x: x[1], reverse=True
            )[:top_k]
            return [
                (self._ids[i], float(score))
                for i, score in top if score > 0
            ]

        if self.backend == 'faiss' and self._encoder is not None:
            import numpy as np
            query_vec = self._encoder.encode(
                [query], normalize_embeddings=True
            ).astype('float32')
            scores, indices = self._index.search(query_vec, top_k)
            return [
                (self._ids[int(i)], float(s))
                for i, s in zip(indices[0], scores[0]) if s > 0
            ]

        return []
