"""
Rulerything 4.0 — 价值传播

将已标注规则的价值向量传播到未标注规则。
使用 BM25 内容相似度（非 value_vector 余弦相似度，避免循环论证）。
"""

import re
from typing import Dict, List, Optional


def propagate_values(
    source_rule,
    candidate_rules: List,
    bm25_index,        # 依赖现有的 semantic_plugin BM25 索引
    category_size: Optional[int] = None,
    threshold: float = 0.6,
    max_propagate: int = 5,
    min_source_confidence: float = 0.7,
    batch_id: Optional[str] = None,  # 传播批次 ID，用于回滚
) -> List[Dict]:
    """
    将已标注规则的价值向量传播到未标注规则。
    使用 BM25 内容相似度（非 value_vector 余弦相似度，避免循环论证）。
    动态上限: max(max_propagate, category_size × 0.1)。
    """
    if source_rule.value_confidence < min_source_confidence:
        return []

    if source_rule.value_source != "manual":
        return []  # 只从人工标注的规则传播

    effective_max = max(max_propagate, int((category_size or 0) * 0.1))

    results = []
    for candidate in candidate_rules:
        if candidate.value_source == "manual":
            continue  # 不覆盖人工标注
        if candidate.id == source_rule.id:
            continue

        # 使用 BM25 内容相似度（非 value_vector 余弦相似度）
        similarity = _bm25_similarity(bm25_index, source_rule, candidate)
        if similarity < threshold:
            continue

        propagated = {
            dim: round(val * similarity, 3)
            for dim, val in source_rule.value_vector.items()
        }

        results.append({
            "target_id": candidate.id,
            "similarity": round(similarity, 3),
            "propagated_vector": propagated,
            "confidence": round(source_rule.value_confidence * similarity, 3),
            "source": "propagated",
            "batch_id": batch_id,
        })

        if len(results) >= effective_max:
            break

    return results


def _bm25_similarity(bm25_index, rule_a, rule_b) -> float:
    """
    BM25 内容相似度，回退到 Jaccard 文本相似度（纯 Python，零依赖）。

    优先使用现有的 semantic_plugin BM25 索引。
    当 semantic_search 不可用时，回退到基于文本 token 的 Jaccard 相似度：
      sim = |tokens(a) ∩ tokens(b)| / |tokens(a) ∪ tokens(b)|
    """
    if bm25_index is not None:
        try:
            return bm25_index.pairwise_similarity(rule_a.id, rule_b.id)
        except Exception:
            pass

    # 回退：Jaccard 文本相似度（中英文混合分词）
    def _tokenize(text: str) -> set:
        result = set()
        # 英文/数字：按单词拆分
        result.update(t.lower() for t in re.findall(r'[a-zA-Z_][a-zA-Z0-9_]+', text))
        # 中文：2-gram 滑动窗口（无词典依赖，可处理任意中文文本）
        chinese_chars = re.findall(r'[\u4e00-\u9fff]', text)
        for i in range(len(chinese_chars) - 1):
            result.add(chinese_chars[i] + chinese_chars[i + 1])
        return {t for t in result if len(t) >= 2}

    tokens_a = _tokenize(rule_a.content)
    tokens_b = _tokenize(rule_b.content)
    if not tokens_a or not tokens_b:
        return 0.0
    return len(tokens_a & tokens_b) / len(tokens_a | tokens_b)
