"""
Rulerything — 查询分析器

职责：切词、归一化、CJK 处理、意图识别。

从 enhanced_index.py 和 index.py 中提取的共享查询分析逻辑。
"""

import re
from typing import List, Set


# ── CJK 检测 ─────────────────────────────────────────────

CJK_RANGE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]")


def has_cjk(text: str) -> bool:
    """检查文本是否包含 CJK 字符。"""
    return bool(CJK_RANGE.search(text))


def is_cjk(char: str) -> bool:
    """判断单个字符是否为 CJK。"""
    return bool(CJK_RANGE.match(char))


# ── 分词 ─────────────────────────────────────────────────

def extract_english_words(text: str) -> Set[str]:
    """提取文本中的英文单词（长度 ≥ 2）。"""
    return {
        word for word in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{1,}", text)
        if len(word) >= 2
    }


def extract_cjk_ngrams(text: str, min_len: int = 2, max_len: int = 4) -> Set[str]:
    """提取 CJK n-gram。"""
    chars = [c for c in text if is_cjk(c)]
    ngrams: Set[str] = set()
    for n in range(min_len, max_len + 1):
        for i in range(len(chars) - n + 1):
            ngrams.add("".join(chars[i:i + n]))
    return ngrams


def extract_words(text: str) -> Set[str]:
    """从文本中提取字母/数字/CJK 词（长度 ≥ 2）。"""
    words: Set[str] = set()
    for token in re.findall(r"[a-zA-Z0-9_]+", text):
        if len(token) >= 2:
            words.add(token.lower())
    for cjk in re.findall(r"[\u4e00-\u9fff\u3400-\u4dbf\uf900-\ufaff]{2,}", text):
        words.add(cjk)
    return words


# ── 停用词 ───────────────────────────────────────────────

SEARCH_STOPWORDS: Set[str] = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from",
    "how", "in", "into", "is", "it", "of", "on", "or", "the", "to",
    "use", "using", "with", "without", "best", "practice", "practices",
    "rule", "rules", "guide", "guideline", "guidelines",
}


def filter_stopwords(terms: Set[str]) -> Set[str]:
    """过滤停用词。"""
    return {t for t in terms if t.lower() not in SEARCH_STOPWORDS}


def query_terms(query: str) -> Set[str]:
    """从查询中提取有意义的搜索词条。"""
    return filter_stopwords(extract_english_words(query))


# ── 查询分析器类 ──────────────────────────────────────────

class QueryAnalyzer:
    """查询分析器 — 切词、归一化、CJK 处理。"""

    @staticmethod
    def analyze(query: str) -> dict:
        """分析查询，返回结构化信息。"""
        return {
            "original": query,
            "lower": query.lower().strip(),
            "has_cjk": has_cjk(query),
            "english_terms": sorted(extract_english_words(query)),
            "cjk_ngrams": sorted(extract_cjk_ngrams(query)),
            "query_terms": sorted(query_terms(query)),
            "length": len(query.strip()),
        }

    @staticmethod
    def term_present(term: str, haystack: str) -> bool:
        """匹配词条（含简单形态变化）。"""
        if term in haystack:
            return True
        if term.endswith("y") and f"{term[:-1]}ies" in haystack:
            return True
        if f"{term}s" in haystack or f"{term}es" in haystack:
            return True
        return False

    @staticmethod
    def compute_coverage(query: str, haystack: str) -> float:
        """计算查询词条在文本中的覆盖率。"""
        terms = query_terms(query)
        if not terms:
            return 1.0
        matched = sum(1 for t in terms if QueryAnalyzer.term_present(t, haystack))
        return matched / len(terms)
