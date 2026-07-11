"""
Rulerything — 规则过滤器

职责：category、scope、status、冲突过滤。

从 index.py 提取，保持行为一致。
"""

from typing import List, Optional, Set

from rule import Rule


class RuleFilter:
    """规则过滤器。"""

    @staticmethod
    def by_category(rules: List[Rule], category: Optional[str] = None) -> List[Rule]:
        """按分类过滤。"""
        if not category:
            return rules
        return [r for r in rules if r.category == category]

    @staticmethod
    def by_lang(rules: List[Rule], lang: Optional[str] = None) -> List[Rule]:
        """按语言过滤。"""
        if not lang:
            return rules
        return [r for r in rules if r.lang == lang]

    @staticmethod
    def by_status(rules: List[Rule], status: Optional[str] = None) -> List[Rule]:
        """按状态过滤（active / deprecated / draft）。"""
        if not status:
            return rules
        return [r for r in rules if getattr(r, 'status', 'active') == status]

    @staticmethod
    def exclude_duplicates(rules: List[Rule]) -> List[Rule]:
        """排除被标记为重复的规则。"""
        return [r for r in rules if not r.is_duplicate]

    @staticmethod
    def exclude_expired(rules: List[Rule]) -> List[Rule]:
        """排除已过期的规则。"""
        return [r for r in rules if not r.is_expired]

    def apply_all(self, rules: List[Rule],
                  category: Optional[str] = None,
                  lang: Optional[str] = None,
                  status: Optional[str] = None,
                  exclude_duplicates: bool = True,
                  exclude_expired: bool = True) -> List[Rule]:
        """批量应用所有过滤规则。"""
        result = rules
        if exclude_duplicates:
            result = self.exclude_duplicates(result)
        if exclude_expired:
            result = self.exclude_expired(result)
        result = self.by_category(result, category)
        result = self.by_lang(result, lang)
        result = self.by_status(result, status)
        return result

    @staticmethod
    def filter_with_reason(rules: List[Rule],
                           category: Optional[str] = None,
                           lang: Optional[str] = None) -> List[Rule]:
        """分类/语言过滤并附带过滤原因。"""
        if not category and not lang:
            return rules
        filtered = []
        for r in rules:
            reasons = []
            if category and r.category != category:
                reasons.append(f"category mismatch (expected {category}, got {r.category})")
            if lang and r.lang != lang:
                reasons.append(f"lang mismatch (expected {lang}, got {r.lang})")
            if not reasons:
                filtered.append(r)
        return filtered
