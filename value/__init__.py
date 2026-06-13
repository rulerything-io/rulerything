"""
Rulerything 4.0 — 价值层入口

懒加载工厂 get_value_engine()，enabled=false 时零模块导入。
"""

from typing import Dict, Optional


class ValueEngine:
    """
    价值引擎 — 持有所有 value 子模块的引用。

    仅在 value.enabled=true 时通过 get_value_engine() 初始化。
    enabled=false 时此对象永远不会被创建，所有 value/*.py 模块零导入。
    """
    def __init__(self, config: dict, storage):
        from .const import VALUE_DIMENSIONS, DEFAULT_VALUE, Signal, CONFLICT_STRATEGIES, default_value_vector, CATEGORY_VALUE_TEMPLATES
        from .vector import cosine_similarity, weighted_sum, dot_product
        from .profile import ValueProfile, load_profiles
        from .weighting import value_weighted_score, sort_rules
        from .conflict import detect_conflicts, resolve_conflicts
        from .decision_trace import generate_decision_trace
        from .exploration import maybe_explore
        from .learning import ValueLearningEngine
        from .propagation import propagate_values
        from .decay import DecayTimer

        self.config = config
        self.storage = storage
        self.profiles = load_profiles(config, storage)
        self.learning = ValueLearningEngine(config.get("learning", {}), storage)
        self.decay_timer = DecayTimer(config.get("learning", {}), self.learning, self.profiles)
        # 引用常量方便外部访问
        self.VALUE_DIMENSIONS = VALUE_DIMENSIONS
        self.Signal = Signal
        self.CATEGORY_VALUE_TEMPLATES = CATEGORY_VALUE_TEMPLATES

        # 引用函数方便外部调用
        self.sort_rules = sort_rules
        self.maybe_explore = maybe_explore
        self.generate_decision_trace = generate_decision_trace
        self.detect_conflicts = detect_conflicts
        self.resolve_conflicts = resolve_conflicts
        self.propagate_values = propagate_values
        self.value_weighted_score = value_weighted_score
        self.cosine_similarity = cosine_similarity
        self.default_value_vector = default_value_vector

    def get_profile(self, name: Optional[str] = None) -> Optional["ValueProfile"]:
        """获取指定名称的画像，未指定时返回 default。"""
        default_profile = self.config.get("default_profile", "default")
        target = name or default_profile
        return self.profiles.get(target)

    def bootstrap_categories(self) -> dict:
        """
        冷启动：为所有 value_source="default" 的规则赋予分类默认向量。
        匹配 CATEGORY_VALUE_TEMPLATES 赋予初始向量。
        """
        from .const import CATEGORY_VALUE_TEMPLATES, default_value_vector
        if self.storage is None:
            return {"bootstrapped": 0, "skipped": 0}

        rules = self.storage.list()
        bootstrapped = 0
        skipped = 0
        for rule in rules:
            if getattr(rule, 'value_source', 'default') != 'default':
                skipped += 1
                continue
            tmpl = CATEGORY_VALUE_TEMPLATES.get(rule.category)
            if tmpl:
                vec = default_value_vector()
                vec.update(tmpl)
                rule.value_vector = vec
                rule.value_confidence = 0.4
                rule.value_source = "bootstrapped"
                try:
                    self.storage.update(
                        rule.id,
                        value_vector=str(rule.value_vector),
                        value_confidence=rule.value_confidence,
                        value_source=rule.value_source,
                    )
                    bootstrapped += 1
                except Exception:
                    import logging
                    logging.exception(f"bootstrap 更新 {rule.id} 失败")
            else:
                skipped += 1

        return {"bootstrapped": bootstrapped, "skipped": skipped}


# 模块级单例（延迟初始化）
_engine: Optional[ValueEngine] = None


def get_value_engine(config: dict = None, storage=None) -> Optional[ValueEngine]:
    """
    获取价值引擎单例。

    如果 config["value"]["enabled"] 为 False，返回 None —— 调用方跳过整个价值层。
    首次调用时初始化所有子模块（延迟导入）。
    """
    global _engine
    if _engine is not None:
        return _engine
    if config is None or not config.get("value", {}).get("enabled", False):
        return None
    _engine = ValueEngine(config["value"], storage)
    return _engine
