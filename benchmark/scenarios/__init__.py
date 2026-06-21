"""
Benchmark — 场景基类和注册表
"""

from typing import Dict, List, Optional


class BenchmarkScene:
    """单个 benchmark 场景基类。"""

    # 场景元数据
    name: str = ""
    description: str = ""
    difficulty: str = ""  # easy / medium / hard
    category: str = ""    # security / performance / async / error-handling / api-design

    def get_task_description(self) -> str:
        """返回任务描述。"""
        raise NotImplementedError

    def get_naive_code(self) -> str:
        """返回无规则指导下的常见实现（可能含缺陷）。"""
        raise NotImplementedError

    def get_improved_code(self) -> str:
        """返回规则指导下改进后的实现。"""
        raise NotImplementedError

    def get_rule_queries(self) -> List[str]:
        """返回用于查询规则系统的关键词列表。"""
        raise NotImplementedError

    def count_naive_bugs(self) -> List[str]:
        """列出无规则代码中的潜在 bug。"""
        raise NotImplementedError

    def count_prevented_bugs(self) -> List[str]:
        """列出规则能预防的 bug。"""
        raise NotImplementedError

    def count_best_practices(self) -> List[str]:
        """列出有规则代码中遵循的最佳实践。"""
        raise NotImplementedError

    def count_edge_cases(self) -> List[str]:
        """列出有规则代码处理的边界情况。"""
        raise NotImplementedError


# 场景注册表
_scenes: Dict[str, BenchmarkScene] = {}


def register(scene: BenchmarkScene):
    _scenes[scene.name] = scene


def get_all() -> List[BenchmarkScene]:
    return list(_scenes.values())


def get(name: str) -> Optional[BenchmarkScene]:
    return _scenes.get(name)


def estimate_tokens(text: str) -> int:
    """估算 token 数（中英文混合：中文约 1.5 字符/token，英文约 4 字符/token）。"""
    import re
    chinese = len(re.findall(r'[\u4e00-\u9fff]', text))
    english = len(re.findall(r'[\x20-\x7E]', text))
    return int(chinese / 1.5 + english / 4) + 1


# 自动导入所有场景（触发 register 调用）
from . import security  # noqa: F401, E402
from . import performance  # noqa: F401, E402
from . import async_python  # noqa: F401, E402
from . import error_handling  # noqa: F401, E402
from . import api_design  # noqa: F401, E402
