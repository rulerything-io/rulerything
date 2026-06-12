# Copyright 2026 Rule-KB Project Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Copyright 2026 Rule-KB Project Authors
"""
TimeDecayCache — Everything 风格的轻量预测性缓存（Phase 3）

Everything 哲学：
- 不搞复杂的时间序列预测
- 用轻量级访问计数 + 时间衰减
- 794 条规则 × 几 KB/条 = 几 MB，全部预热也无压力
- 这里的"缓存"本质是"内存常驻优先级标记"
"""

import time
import random
from typing import Dict, List, Optional, Callable, Set
from collections import defaultdict


class TimeDecayCache:
    """
    时间衰减热度缓存。

    核心参数（configurable）：
    - decay_half_life: 半衰期秒数（默认 3600 = 1 小时）
    - preheat_threshold: 热度 ≥ 此值自动预热
    - max_size: 缓存容量上限

    使用方式：
        cache = TimeDecayCache()
        cache.record_access("python/001")
        asyncio.create_task(cache.auto_preheat(loader, all_ids))
    """

    def __init__(
        self,
        max_size: int = 5000,
        decay_half_life: float = 3600.0,
        preheat_threshold: float = 1.0,
    ):
        self.max_size = max_size
        self.decay_half_life = decay_half_life
        self.preheat_threshold = preheat_threshold

        self.cache: Dict[str, object] = {}
        self.heat: Dict[str, float] = defaultdict(float)
        self.last_decay: Dict[str, float] = {}

    def record_access(self, rule_id: str):
        """记录一次访问，热度 +1，缓存超量时自动淘汰。"""
        now = time.time()
        self._apply_decay(rule_id, now)
        self.heat[rule_id] += 1.0
        self.last_decay[rule_id] = now
        if len(self.cache) > self.max_size:
            self.evict_if_needed()

    def _apply_decay(self, rule_id: str, now: float):
        """应用指数时间衰减。"""
        last = self.last_decay.get(rule_id, now)
        elapsed = now - last
        if elapsed <= 0:
            return
        factor = 2 ** (-elapsed / self.decay_half_life)
        self.heat[rule_id] *= factor

    def get_heat(self, rule_id: str) -> float:
        """获取当前热度（自动衰减）。"""
        self._apply_decay(rule_id, time.time())
        return self.heat.get(rule_id, 0.0)

    async def auto_preheat(
        self,
        rule_loader: Callable[[str], object],
        all_rule_ids: Set[str],
    ) -> List[str]:
        """
        自动预热：热度达到阈值的规则加载到缓存。

        Everything 风格：不预测未来，只看当前热度。
        """
        now = time.time()
        for rid in all_rule_ids:
            self._apply_decay(rid, now)

        sorted_by_heat = sorted(
            self.heat.items(), key=lambda x: x[1], reverse=True
        )
        preheat_list = []
        for rid, heat_score in sorted_by_heat:
            if len(self.cache) >= self.max_size:
                break
            if heat_score >= self.preheat_threshold and rid not in self.cache:
                rule = rule_loader(rid)
                if rule:
                    self.cache[rid] = rule
                    preheat_list.append(rid)

        # 随机探索：加载从未访问的规则（可配置）
        never_accessed = all_rule_ids - set(self.last_decay.keys())
        if never_accessed and len(self.cache) < self.max_size * 0.8:
            n_random = max(1, min(
                int(len(never_accessed) * 0.05),
                self.max_size - len(self.cache),
                len(never_accessed),
            ))
            for rid in random.sample(list(never_accessed), n_random):
                rule = rule_loader(rid)
                if rule:
                    self.cache[rid] = rule
                    preheat_list.append(rid)

        return preheat_list

    def evict_if_needed(self) -> List[str]:
        """淘汰热度最低的规则。"""
        if len(self.cache) <= self.max_size:
            return []

        now = time.time()
        for rid in list(self.cache):
            self._apply_decay(rid, now)

        to_evict = sorted(
            self.cache, key=lambda rid: self.heat.get(rid, 0)
        )[:len(self.cache) - self.max_size]

        for rid in to_evict:
            del self.cache[rid]
            self.heat.pop(rid, None)
            self.last_decay.pop(rid, None)

        return to_evict
