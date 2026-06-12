# Copyright 2026 rulerything-io
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

# Copyright 2026 rulerything-io
"""
规则实体 — Rulerything 的核心数据模型
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import List, Optional
import hashlib


@dataclass
class Rule:
    # 标识字段
    id: str                         # 唯一标识 "category/seq"
    title: str                      # 规则标题，用于精确匹配
    content: str                    # 规则正文

    # 分类字段
    category: str = "philosophy"    # philosophy|pattern|performance|security
    tags: List[str] = field(default_factory=list)

    # 质量字段
    confidence: float = 0.5         # 0~1，初始 0.5
    verifier: str = "manual"        # manual|auto|crowd

    # 版本与进化
    version: int = 1
    parent_id: Optional[str] = None
    duplicate_of: Optional[str] = None
    evolution_log: List[str] = field(default_factory=list)

    # 时效
    created_at: Optional[datetime] = None
    last_verified: Optional[datetime] = None
    expires_at: Optional[datetime] = None

    # 使用统计（自动更新）
    hit_count: int = 0
    last_hit: Optional[datetime] = None

    # v3.0 字段
    ai_verified: bool = False
    last_ai_review: Optional[datetime] = None

    def __post_init__(self):
        if self.created_at is None:
            self.created_at = datetime.now()

    @property
    def content_hash(self) -> str:
        """内容 SHA256，用于去重检测。"""
        return hashlib.sha256(
            self.content.strip().encode("utf-8")
        ).hexdigest()

    @property
    def is_expired(self) -> bool:
        """是否已过期。"""
        if self.expires_at is None:
            return False
        return datetime.now() >= self.expires_at

    @property
    def is_duplicate(self) -> bool:
        """是否被标记为重复（指向主规则）。"""
        return self.duplicate_of is not None

    @property
    def effective_id(self) -> str:
        """实际生效的规则 ID（跟随重定向）。"""
        return self.duplicate_of or self.id

    def to_dict(self) -> dict:
        """序列化为 JSON 兼容字典。"""
        d = asdict(self)
        # datetime → ISO 字符串
        for key in ("created_at", "last_verified", "last_hit", "expires_at", "last_ai_review"):
            val = d.get(key)
            if isinstance(val, datetime):
                d[key] = val.isoformat()
        # 注入 content_hash
        d["content_hash"] = self.content_hash
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Rule":
        """从 JSON 兼容字典反序列化。"""
        # 兼容旧数据：content_hash 由 property 动态计算
        d.pop("content_hash", None)
        # ISO 字符串 → datetime
        for key in ("created_at", "last_verified", "last_hit", "expires_at", "last_ai_review"):
            val = d.get(key)
            if isinstance(val, str):
                d[key] = datetime.fromisoformat(val)
        return cls(**d)

    def record_hit(self):
        """记录一次命中。"""
        self.hit_count += 1
        self.last_hit = datetime.now()

    def evolve(self, log_entry: str):
        """记录一次进化。"""
        self.version += 1
        self.evolution_log.append(log_entry)
