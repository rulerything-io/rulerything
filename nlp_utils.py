"""
Rulerything — 共享 NLP 工具函数

集中管理所有分词、相似度计算、CJK 处理函数，消除跨模块重复。

用法:
    from nlp_utils import tokenize, cosine_similarity, has_cjk
"""

import math
import re
from collections import Counter
from typing import Dict, List, Optional, Set, Tuple


# ── CJK 检测 ─────────────────────────────────────────────

_CJK_RANGES = (
    (0x4E00, 0x9FFF),   # CJK 统一表意文字
    (0x3400, 0x4DBF),   # CJK 扩展 A
    (0x2E80, 0x2EFF),   # CJK 部首
    (0x3000, 0x303F),   # CJK 符号和标点
)


def is_cjk(char: str) -> bool:
    """判断单个字符是否为 CJK。"""
    cp = ord(char)
    return any(lo <= cp <= hi for lo, hi in _CJK_RANGES)


def has_cjk(text: str) -> bool:
    """判断文本是否包含 CJK 字符。"""
    return any(is_cjk(ch) for ch in text)


# ── 分词 ─────────────────────────────────────────────────


def tokenize(text: str, min_len: int = 2) -> List[str]:
    """通用分词：提取英文/数字词组 + CJK 二元组。

    Args:
        text: 输入文本
        min_len: 最小词长度

    Returns:
        词元列表
    """
    tokens: List[str] = []
    text_lower = text.lower()
    # 英文/数字/符号词
    for word in re.findall(r'[a-z_+#.0-9]+', text_lower):
        if len(word) >= min_len:
            tokens.append(word)
    # CJK 二元组
    cjk_seq = re.findall(r'[\u4e00-\u9fff]+', text)
    for seq in cjk_seq:
        for i in range(len(seq) - 1):
            tokens.append(seq[i:i + 2])
    return tokens


def tokenize_set(text: str, min_len: int = 2) -> Set[str]:
    """分词并返回集合（去重）。"""
    return set(tokenize(text, min_len=min_len))


def extract_words(text: str, min_len: int = 2) -> Set[str]:
    """提取文本中的所有词汇（用于倒排索引），字母/数字/CJK 均支持。"""
    return {w for w in re.findall(r'[\w\u4e00-\u9fff]+', text.lower()) if len(w) >= min_len}


# ── CJK n-gram ───────────────────────────────────────────


def extract_cjk_ngrams(text: str, min_len: int = 2) -> List[str]:
    """提取连续 CJK 序列及其 n-gram。

    '性能优化' (min_len=2) → ['性能','能优','优化','性能优','能优化','性能优化']
    """
    seqs = []
    cur: List[str] = []
    for ch in text:
        if is_cjk(ch):
            cur.append(ch)
        else:
            if len(cur) >= min_len:
                seqs.append(''.join(cur))
            cur = []
    if len(cur) >= min_len:
        seqs.append(''.join(cur))

    ngrams: List[str] = []
    for seq in seqs:
        for i in range(len(seq)):
            for j in range(i + min_len, len(seq) + 1):
                ngrams.append(seq[i:j])
    return ngrams


def extract_english_words(text: str, min_len: int = 2) -> List[str]:
    """从可能混合 CJK 的文本中提取纯英文字词。"""
    words: List[str] = []
    cur: List[str] = []
    for ch in text:
        if not is_cjk(ch) and ch.isalpha() and ch.isascii():
            cur.append(ch.lower())
        else:
            if len(cur) >= min_len:
                words.append(''.join(cur))
            cur = []
    if len(cur) >= min_len:
        words.append(''.join(cur))
    return words


# ── 余弦相似度 ───────────────────────────────────────────


def cosine_similarity(vec_a: Dict[str, float], vec_b: Dict[str, float]) -> float:
    """计算两个 TF/IDF 向量的余弦相似度。"""
    intersection = set(vec_a) & set(vec_b)
    if not intersection:
        return 0.0
    dot = sum(vec_a[k] * vec_b[k] for k in intersection)
    norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v * v for v in vec_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ── Jaccard 相似度 ───────────────────────────────────────


def jaccard_similarity(text: str, titles: List[str]) -> float:
    """计算文本与所有标题的最大 Jaccard 相似度。"""
    text_tokens = extract_words(text)
    if not text_tokens:
        return 0.0

    max_sim = 0.0
    for title in titles[:100]:
        title_tokens = extract_words(title)
        if not title_tokens:
            continue
        sim = len(text_tokens & title_tokens) / max(len(text_tokens | title_tokens), 1)
        if sim > max_sim:
            max_sim = sim
    return max_sim


# ── 纯 Python TF-IDF ─────────────────────────────────────


def pure_python_tfidf(texts: List[str]) -> Tuple[List[Dict[str, float]], List[str]]:
    """纯 Python TF-IDF 实现（无外部依赖）。

    Returns:
        (vectors, vocabulary)
        vectors: 每个文本的 {term: weight} 字典列表
        vocabulary: 所有词项（有序）
    """
    tokenized = [tokenize(text) for text in texts]

    # 词频
    tfs = []
    for tokens in tokenized:
        counter = Counter(tokens)
        total = len(tokens) if tokens else 1
        tfs.append({w: c / total for w, c in counter.items()})

    # 文档频率
    df: Counter = Counter()
    for tokens in tokenized:
        for w in set(tokens):
            df[w] += 1

    # IDF
    n = len(texts)
    idf = {w: math.log(n / (1 + freq)) + 1 for w, freq in df.items()}

    # TF-IDF
    vectors = []
    for tf in tfs:
        vec = {w: tf[w] * idf.get(w, 1) for w in tf}
        vectors.append(vec)

    all_terms = sorted(idf.keys())
    return vectors, all_terms
