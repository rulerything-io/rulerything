# Copyright 2026 Rulerything Project Authors
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

# Copyright 2026 Rulerything Project Authors
"""
AIBridge — AI 桥接层（Phase C）

核心机制：
  - 多 Provider 抽象（Claude / OpenAI / Local）
  - 预算控制（每日硬上限，乐观锁防超支）
  - 调用缓存（规则版本哈希自动失效）
  - 回答验证（TF-IDF 与规则库交叉验证）

Everything 原则：
  - 默认关闭，不影响核心查询路径
  - LLM 不可用 / 预算耗尽 → 平滑降级为纯系统检索

用法:
    bridge = AIBridge(storage, config)
    result = bridge.enhance_query("如何在 Python 中...")
"""

import abc
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, date
from typing import Any, Dict, List, Optional, Tuple


# ── 辅助 ────────────────────────────────────────────────

def _iso_now() -> str:
    return datetime.now().isoformat()


# ── 验证结果枚举 ────────────────────────────────────────

class AIValidationResult:
    """AI 回答验证结果。"""
    CONSISTENT = "consistent"      # 与规则库高度一致
    PARTIAL = "partial"            # 部分一致
    UNVERIFIABLE = "unverifiable"  # 规则库无相关知识
    # 注意：故意不定义 CONTRADICT — 纯 TF-IDF 无法可靠检测矛盾
    # 误标矛盾比不标更危险


# ── 预算控制 ────────────────────────────────────────────

class AIBudget:
    """每日预算控制（乐观锁防超支）。

    逻辑：
      - 每日重置：检测 date.today() 变化时重置 cost_today
      - 硬上限：allow_call() 用 SQLite 乐观锁，原子 UPDATE 防超支
      - 惰性持久化：每 5 次调用同步一次 config_runtime，减少写放大
      - 进程恢复：启动时从 config_runtime 读取今日已用

    用法:
        budget = AIBudget(storage, daily_limit=5.0)
        if budget.allow_call():
            cost = do_call()
            budget.record_cost(cost)
    """

    def __init__(self, storage, config: dict):
        self.storage = storage
        self.daily_limit = config.get("daily_limit_usd", 5.0)
        self.per_call_limit = config.get("per_call_limit_usd", 0.5)
        self.warn_threshold = config.get("warn_threshold", 0.8)

        # 运行时状态
        self._today = date.today()
        self._cost_today = 0.0
        self._call_count = 0
        self._last_sync = 0
        self._sync_interval = config.get("budget_sync_interval", 5)
        self._warned = False
        self._budget_lock = threading.Lock()

        # 从持久化恢复
        self._load()

    def _load(self):
        """从 config_runtime 恢复当日已用额度。"""
        try:
            raw = self.storage.get_config("ai_cost_today", "0.0")
            saved_date = self.storage.get_config("ai_cost_date", "")
            if saved_date == str(self._today):
                self._cost_today = float(raw)
        except Exception:
            pass

    def _save(self):
        """同步到 config_runtime（每 N 次调用写一次）。"""
        self._call_count += 1
        if self._call_count - self._last_sync < self._sync_interval:
            return
        self._last_sync = self._call_count
        try:
            self.storage.set_config("ai_cost_today", str(round(self._cost_today, 6)))
            self.storage.set_config("ai_cost_date", str(self._today))
        except Exception:
            pass

    def _check_reset(self):
        """检测日期变更，自动重置。"""
        today = date.today()
        if today > self._today:
            self._cost_today = 0.0
            self._warned = False
            self._today = today
            try:
                self.storage.set_config("ai_cost_today", "0.0")
                self.storage.set_config("ai_cost_date", str(today))
            except Exception:
                pass

    def allow_call(self, estimated_cost: float = 0.05) -> bool:
        """检查是否可以调用 LLM。

        使用线程锁防止并发超支。

        Args:
            estimated_cost: 预估调用成本，预扣用

        Returns:
            True=允许调用，False=预算不足
        """
        self._check_reset()

        if estimated_cost > self.per_call_limit:
            return False

        with self._budget_lock:
            if self._cost_today + estimated_cost > self.daily_limit:
                return False

        return True

    def record_cost(self, cost_usd: float):
        """记录实际调用成本。"""
        if cost_usd < 0:
            return
        with self._budget_lock:
            self._check_reset()
            self._cost_today += cost_usd
            self._save()

            # 阈值告警
            ratio = self._cost_today / max(self.daily_limit, 0.01)
        if ratio >= self.warn_threshold and not self._warned:
            self._warned = True
            self.storage.log_audit(
                action="ai_budget_warn",
                module="ai_bridge",
                result="warning",
                error_message=f"AI 预算已用 {ratio:.0%} (${self._cost_today:.2f}/${self.daily_limit:.2f})",
            )

    def get_status(self) -> dict:
        """当前预算状态。"""
        self._check_reset()
        return {
            "daily_limit": self.daily_limit,
            "cost_today": round(self._cost_today, 4),
            "remaining": round(max(self.daily_limit - self._cost_today, 0), 4),
            "usage_ratio": round(self._cost_today / max(self.daily_limit, 0.01), 4),
            "exhausted": self._cost_today >= self.daily_limit,
            "reset_date": str(self._today),
        }


# ── LLM Provider 抽象 ──────────────────────────────────

class AIProvider(abc.ABC):
    """LLM 提供者抽象基类。"""

    @abc.abstractmethod
    def chat(self, messages: List[dict], max_tokens: int = 1024,
             temperature: float = 0.3) -> dict:
        """调用 LLM 聊天补全。

        Returns:
            {
                "content": str,
                "model": str,
                "usage": {"prompt_tokens": int, "completion_tokens": int},
                "cost_usd": float,
            }
        """
        ...

    @abc.abstractmethod
    def estimate_cost(self, prompt_chars: int) -> float:
        """估算一次调用的成本（调用前用于预算检查）。"""
        ...

    @classmethod
    def from_config(cls, config: dict) -> "AIProvider":
        """工厂方法。"""
        provider = config.get("provider", "claude").lower()
        if provider == "openai":
            return OpenAIProvider(config)
        elif provider == "deepseek":
            return DeepSeekProvider(config)
        elif provider == "local":
            return LocalProvider(config)
        return ClaudeProvider(config)


class ClaudeProvider(AIProvider):
    """Anthropic Claude API。

    成本模型 (per M tokens):
      claude-sonnet-4-6:  $3.00 input / $15.00 output
      claude-haiku-4-5:   $1.00 input / $5.00 output
    """

    MODEL_COSTS = {
        "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
        "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    }

    def __init__(self, config: dict):
        self.endpoint = config.get("endpoint") or "https://api.anthropic.com/v1/messages"
        self.model = config.get("model", "claude-sonnet-4-6")
        self.api_key = os.environ.get(config.get("api_key_env", "ANTHROPIC_API_KEY"), "")
        self.costs = self.MODEL_COSTS.get(self.model, {"input": 3.0, "output": 15.0})

    def chat(self, messages: List[dict], max_tokens: int = 1024,
             temperature: float = 0.3) -> dict:
        """Claude Messages API 调用。"""
        body = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }).encode()

        req = urllib.request.Request(
            self.endpoint, data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )

        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            raise RuntimeError(f"Claude API 调用失败: {e}") from e

        latency = time.perf_counter() - start
        content = ""
        usage = {"prompt_tokens": 0, "completion_tokens": 0}

        if isinstance(data, dict):
            if "content" in data and isinstance(data["content"], list):
                texts = [b.get("text", "") for b in data["content"] if b.get("type") == "text"]
                content = "\n".join(texts)
            usage = data.get("usage", usage) or usage

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cost = self._compute_cost(prompt_tokens, completion_tokens)

        return {
            "content": content.strip(),
            "model": self.model,
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            "cost_usd": cost,
            "latency_ms": round(latency * 1000, 2),
        }

    def _compute_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens / 1_000_000 * self.costs["input"]
                + completion_tokens / 1_000_000 * self.costs["output"])

    def estimate_cost(self, prompt_chars: int) -> float:
        """按字符数粗略估算成本（~4 chars/token）。"""
        est_tokens = prompt_chars // 4 + 100  # +100 系统 prompt
        return est_tokens / 1_000_000 * self.costs["input"] * 1.5  # *1.5 预估 output


class OpenAIProvider(AIProvider):
    """OpenAI 兼容 API（OpenAI / Azure / vLLM）。"""

    MODEL_COSTS = {
        "gpt-4o": {"input": 2.5, "output": 10.0},
        "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    }

    def __init__(self, config: dict):
        self.endpoint = config.get("endpoint") or "https://api.openai.com/v1/chat/completions"
        self.model = config.get("model", "gpt-4o-mini")
        self.api_key = os.environ.get(config.get("api_key_env", "OPENAI_API_KEY"), "")
        self.costs = self.MODEL_COSTS.get(self.model, {"input": 0.15, "output": 0.6})

    def chat(self, messages: List[dict], max_tokens: int = 1024,
             temperature: float = 0.3) -> dict:
        body = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }).encode()

        req = urllib.request.Request(
            self.endpoint, data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            raise RuntimeError(f"OpenAI API 调用失败: {e}") from e

        latency = time.perf_counter() - start
        content = ""
        usage = {"prompt_tokens": 0, "completion_tokens": 0}

        if isinstance(data, dict):
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
            usage = data.get("usage", usage) or usage

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cost = self._compute_cost(prompt_tokens, completion_tokens)

        return {
            "content": content.strip(),
            "model": self.model,
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            "cost_usd": cost,
            "latency_ms": round(latency * 1000, 2),
        }

    def _compute_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens / 1_000_000 * self.costs["input"]
                + completion_tokens / 1_000_000 * self.costs["output"])

    def estimate_cost(self, prompt_chars: int) -> float:
        est_tokens = prompt_chars // 4 + 100
        return est_tokens / 1_000_000 * self.costs["input"] * 1.5


class DeepSeekProvider(AIProvider):
    """DeepSeek API（OpenAI 兼容接口）。

    成本模型 (per M tokens):
      deepseek-chat:     $0.14 input / $0.28 output
      deepseek-reasoner: $0.55 input / $1.19 output（含推理 token）
    """

    MODEL_COSTS = {
        "deepseek-chat": {"input": 0.14, "output": 0.28},
        "deepseek-reasoner": {"input": 0.55, "output": 1.19},
        "deepseek-v4-flash": {"input": 0.20, "output": 0.40},
    }

    def __init__(self, config: dict):
        self.endpoint = config.get("endpoint") or "https://api.deepseek.com/v1/chat/completions"
        self.model = config.get("model", "deepseek-chat")
        self.api_key = os.environ.get(config.get("api_key_env", "DEEPSEEK_API_KEY"), "")
        self.costs = self.MODEL_COSTS.get(self.model, {"input": 0.14, "output": 0.28})

    def chat(self, messages: List[dict], max_tokens: int = 1024,
             temperature: float = 0.3) -> dict:
        body = json.dumps({
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": messages,
        }).encode()

        req = urllib.request.Request(
            self.endpoint, data=body,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )

        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            raise RuntimeError(f"DeepSeek API 调用失败: {e}") from e

        latency = time.perf_counter() - start
        content = ""
        usage = {"prompt_tokens": 0, "completion_tokens": 0}

        if isinstance(data, dict):
            choices = data.get("choices", [])
            if choices:
                content = choices[0].get("message", {}).get("content", "")
            usage = data.get("usage", usage) or usage

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        cost = self._compute_cost(prompt_tokens, completion_tokens)

        return {
            "content": content.strip(),
            "model": self.model,
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
            "cost_usd": cost,
            "latency_ms": round(latency * 1000, 2),
        }

    def _compute_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        return (prompt_tokens / 1_000_000 * self.costs["input"]
                + completion_tokens / 1_000_000 * self.costs["output"])

    def estimate_cost(self, prompt_chars: int) -> float:
        est_tokens = prompt_chars // 4 + 100
        return est_tokens / 1_000_000 * self.costs["input"] * 1.5


class LocalProvider(AIProvider):
    """本地模型（ollama / vLLM），成本为零。"""

    def __init__(self, config: dict):
        self.endpoint = config.get("endpoint") or "http://localhost:11434/api/chat"
        self.model = config.get("model", "qwen2.5:7b")

    def chat(self, messages: List[dict], max_tokens: int = 1024,
             temperature: float = 0.3) -> dict:
        body = json.dumps({
            "model": self.model,
            "options": {"num_predict": max_tokens, "temperature": temperature},
            "messages": messages,
            "stream": False,
        }).encode()

        req = urllib.request.Request(
            self.endpoint, data=body,
            headers={"Content-Type": "application/json"},
        )

        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
            raise RuntimeError(f"本地模型调用失败: {e}") from e

        latency = time.perf_counter() - start
        content = data.get("message", {}).get("content", "") if isinstance(data, dict) else ""

        return {
            "content": content.strip(),
            "model": self.model,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            "cost_usd": 0.0,
            "latency_ms": round(latency * 1000, 2),
        }

    def estimate_cost(self, prompt_chars: int) -> float:
        return 0.0  # 本地模型零成本


# ── AI 回答验证 ─────────────────────────────────────────

class AIValidator:
    """用规则库交叉验证 AI 回答（TF-IDF 相似度）。

    流程：
      1. 将 AI 回答分段（按句号/换行分割）
      2. 每段与规则库标题/内容计算 TF-IDF 余弦相似度
      3. 汇总判定：max_sim ≥ 0.7 → CONSISTENT
                    0.3 ~ 0.7 → PARTIAL
                    < 0.3 → UNVERIFIABLE

    注意：不检测 CONTRADICT — TF-IDF 无法可靠识别"矛盾"。
    """

    def __init__(self, storage, gap_detector=None):
        self.storage = storage
        self._gap = gap_detector  # 复用 gap_detector 的 TF-IDF 方法

    def validate(self, ai_response: str) -> dict:
        """验证 AI 回答与规则库的一致性。

        Args:
            ai_response: AI 生成的回答文本

        Returns:
            {
                "result": "consistent" | "partial" | "unverifiable",
                "max_similarity": float,
                "matched_rules": [str, ...],
                "confidence": float,
            }
        """
        if not ai_response or not ai_response.strip():
            return {
                "result": AIValidationResult.UNVERIFIABLE,
                "max_similarity": 0.0,
                "matched_rules": [],
                "confidence": 0.0,
            }

        # 获取规则标题列表
        rules = self.storage.list()
        if not rules:
            return {
                "result": AIValidationResult.UNVERIFIABLE,
                "max_similarity": 0.0,
                "matched_rules": [],
                "confidence": 0.0,
            }

        titles = [r.title for r in rules]

        # 复用 gap_detector 的 TF-IDF 计算（如果可用）
        max_sim = self._compute_max_similarity(ai_response, titles)

        # 分段：按句子分割，取最长 3 段分别匹配
        segments = [s.strip() for s in ai_response.replace("\n", "。").split("。") if len(s.strip()) > 10]
        matched_rules = set()
        for seg in segments[:3]:
            seg_sim = self._compute_max_similarity(seg, titles)
            if seg_sim >= 0.5:
                # 找到最匹配的规则
                best_title = self._find_best_match(seg, rules)
                if best_title:
                    matched_rules.add(best_title)

        # 汇总判定
        if max_sim >= 0.7:
            result = AIValidationResult.CONSISTENT
        elif max_sim >= 0.3:
            result = AIValidationResult.PARTIAL
        else:
            result = AIValidationResult.UNVERIFIABLE

        return {
            "result": result,
            "max_similarity": round(max_sim, 4),
            "matched_rules": list(matched_rules)[:5],
            "confidence": round(max_sim * 0.8 + 0.2, 4),  # 保守映射
        }

    def _compute_max_similarity(self, text: str, titles: List[str]) -> float:
        """计算文本与所有标题的最大余弦相似度。"""
        if not text or not titles:
            return 0.0

        try:
            # 尝试复用 gap_detector
            if self._gap and hasattr(self._gap, "_max_similarity"):
                return self._gap._max_similarity(text, titles)
        except Exception:
            pass

        # 纯 Python 简易匹配：词袋 + Jaccard
        return self._jaccard_similarity(text, titles)

    def _find_best_match(self, text: str, rules) -> Optional[str]:
        """找到最匹配的规则标题。"""
        text_tokens = set(self._simple_tokenize(text))
        if not text_tokens:
            return None

        best_sim = 0.0
        best_id = None
        for r in rules:
            title_tokens = set(self._simple_tokenize(r.title))
            if not title_tokens:
                continue
            sim = len(text_tokens & title_tokens) / max(len(text_tokens | title_tokens), 1)
            if sim > best_sim:
                best_sim = sim
                best_id = r.id
        return best_id

    @staticmethod
    def _simple_tokenize(text: str) -> List[str]:
        """简易分词：英文单词 + CJK 二元组。"""
        import re
        tokens = []
        text_lower = text.lower()
        for word in re.findall(r'[a-z_+#0-9]+', text_lower):
            if len(word) >= 2:
                tokens.append(word)
        cjk_seq = re.findall(r'[\u4e00-\u9fff]+', text)
        for seq in cjk_seq:
            for i in range(len(seq) - 1):
                tokens.append(seq[i:i + 2])
        return tokens

    @staticmethod
    def _jaccard_similarity(text: str, titles: List[str]) -> float:
        """Jaccard 相似度（降级方案）。"""
        import re
        text_tokens = set()
        for word in re.findall(r'[a-z_+#0-9\u4e00-\u9fff]+', text.lower()):
            if len(word) >= 2:
                text_tokens.add(word)

        if not text_tokens:
            return 0.0

        max_sim = 0.0
        for title in titles[:100]:  # 前 100 条足够
            title_tokens = set()
            for word in re.findall(r'[a-z_+#0-9\u4e00-\u9fff]+', title.lower()):
                if len(word) >= 2:
                    title_tokens.add(word)
            if not title_tokens:
                continue
            sim = len(text_tokens & title_tokens) / max(len(text_tokens | title_tokens), 1)
            if sim > max_sim:
                max_sim = sim
        return max_sim


# ── AI 调用缓存 ─────────────────────────────────────────

class AICache:
    """AI 调用缓存（DB 持久化 + 规则版本自动失效）。"""

    def __init__(self, storage, config: dict):
        self.storage = storage
        self.ttl_hours = config.get("cache_ttl_hours", 24)
        self._rule_version = ""

    def _get_version_key(self, query: str) -> str:
        """生成包含规则版本哈希的缓存键。"""
        try:
            current = self.storage.get_rule_version_hash()
        except Exception:
            current = ""
        self._rule_version = current or self._rule_version
        raw = f"{query}|v{self._rule_version}"
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def lookup(self, query: str) -> Optional[dict]:
        """查询缓存。命中则增加 hit_count。"""
        key = self._get_version_key(query)
        try:
            entry = self.storage.ai_cache_get(key, ttl_hours=self.ttl_hours)
        except Exception:
            return None

        if entry:
            try:
                self.storage.ai_cache_hit(key)
            except Exception:
                pass
            return {
                "query": entry["query"],
                "content": entry["response"],
                "model": entry["model"],
                "validation": json.loads(entry["validation"]) if entry.get("validation") else None,
                "cost_usd": entry["cost_usd"],
                "latency_ms": entry["latency_ms"],
                "hit_count": entry["hit_count"] + 1,
            }
        return None

    def store(self, query: str, response: str, model: str,
              cost_usd: float, latency_ms: float,
              validation: Optional[dict] = None):
        """写入缓存。"""
        key = self._get_version_key(query)
        try:
            self.storage.ai_cache_set(
                query_hash=key,
                query=query,
                response=response,
                model=model,
                cost_usd=cost_usd,
                latency_ms=latency_ms,
                validation=json.dumps(validation, ensure_ascii=False) if validation else None,
            )
        except Exception:
            pass


# ── 统一入口 ────────────────────────────────────────────

class AIBridge:
    """AI 桥接层统一入口。

    查询增强流程:
      1. 系统检索 → 置信度 ≥ threshold? → 直接返回
      2. 预算检查 → 不足 → 返回系统结果 + 标注
      3. 缓存查询 → 命中 → 返回缓存（不计费）
      4. LLM 调用 → 验证 → 扣减预算 → 缓存 → 返回
    """

    def __init__(self, storage, config: dict, index=None, gap_detector=None):
        self.storage = storage
        self.config = config
        self.enabled = config.get("enabled", False)
        self.confidence_threshold = config.get("confidence_threshold", 0.6)

        # 可选的索引引用（用于搜索上下文注入）
        self._index = index

        # 子模块
        self.budget = AIBudget(storage, config)
        self.provider = AIProvider.from_config(config)
        self.cache = AICache(storage, config)
        self.validator = AIValidator(storage, gap_detector=gap_detector)

        # 多轮对话历史 {conversation_id: [{"role":..., "content":...}, ...]}
        self.conversations: Dict[str, List[dict]] = {}
        self._MAX_CONVERSATION_TURNS = 10  # 最多保留近 5 轮（10 条消息）

        # 线程锁
        self._stats_lock = threading.Lock()

        # 统计
        self.stats = {
            "total_queries": 0,
            "ai_calls": 0,
            "cache_hits": 0,
            "budget_skips": 0,
            "errors": 0,
            "total_cost": 0.0,
            "delegated_queries": 0,
        }

    def _inc_stat(self, key: str, delta: float = 1):
        """线程安全地更新统计计数器。"""
        with self._stats_lock:
            self.stats[key] = self.stats.get(key, 0) + delta

    def is_enabled(self) -> bool:
        """检查 AI Bridge 是否可用。"""
        if not self.enabled:
            return False
        # 委托模式不需要 API key
        if self.config.get("use_parent_ai"):
            return True
        # 检查 API key 是否配置
        key = os.environ.get(self.config.get("api_key_env", "ANTHROPIC_API_KEY"), "")
        if not key and self.config.get("provider", "claude") != "local":
            return False
        return True

    def enhance_query(self, query: str, search_context: Optional[dict] = None,
                       conversation_id: Optional[str] = None) -> dict:
        """AI 增强查询。

        Args:
            query: 用户问题
            search_context: 可选的搜索上下文（索引结果、分类等）
            conversation_id: 可选的多轮对话 ID，留空则每次独立

        Returns:
            {
                "query": str,
                "source": "ai" | "system" | "cache" | "delegated",
                "content": str,
                "confidence": float,
                "validation": {...},
                "ai_model": str | None,
                "cost_usd": float,
                "latency_ms": float,
                "conversation_id": str | None,
            }
        """
        # 返回格式基座
        result = {
            "query": query,
            "source": "system",
            "content": "",
            "confidence": 0.0,
            "validation": None,
            "ai_model": None,
            "cost_usd": 0.0,
            "latency_ms": 0.0,
            "conversation_id": conversation_id,
        }
        self._inc_stat("total_queries")

        if not self.is_enabled():
            # 诊断原因
            if not self.enabled:
                result["error"] = "AI Bridge 未启用（config.ai_bridge.enabled = false）"
            else:
                key = os.environ.get(self.config.get("api_key_env", "ANTHROPIC_API_KEY"), "")
                if not key:
                    result["error"] = (
                        f"API Key 未配置。请在 AI 设置页面填写，或设置环境变量 "
                        f"{self.config.get('api_key_env', 'ANTHROPIC_API_KEY')}"
                    )
                else:
                    result["error"] = "AI Bridge 不可用（未知原因）"
            return result

        # 父 AI 委托模式：将查询存入待处理队列，由外部 AI 回答
        if self.config.get("use_parent_ai"):
            try:
                query_id = self.storage.add_pending_query(query)
            except Exception:
                query_id = "unknown"
            self._inc_stat("delegated_queries")
            result.update({
                "source": "delegated",
                "content": f"查询已委托给父 AI (query_id={query_id})",
                "query_id": query_id,
                "confidence": 0.0,
            })
            return result

        start = time.perf_counter()

        # 预算检查
        est_cost = self.provider.estimate_cost(len(query))
        if not self.budget.allow_call(est_cost):
            self._inc_stat("budget_skips")
            result["content"] = "AI 预算已耗尽，当前为系统检索结果"
            result["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
            return result

        # 缓存查询（仅独立查询走缓存，多轮对话不缓存）
        if not conversation_id:
            cached = self.cache.lookup(query)
            if cached:
                self._inc_stat("cache_hits")
                result.update({
                    "source": "cache",
                    "content": cached["content"],
                    "confidence": 0.7,
                    "validation": cached.get("validation"),
                    "ai_model": cached["model"],
                    "cost_usd": cached["cost_usd"],
                    "latency_ms": cached["latency_ms"],
                })
                return result

        # LLM 调用
        try:
            response = self._call_llm(query, search_context=search_context,
                                       conversation_id=conversation_id)
        except RuntimeError as e:
            self._inc_stat("errors")
            result["error"] = f"LLM 调用失败: {e}"
            result["latency_ms"] = round((time.perf_counter() - start) * 1000, 2)
            return result

        llm_latency = response.get("latency_ms", 0)
        cost_usd = response.get("cost_usd", 0)

        # 验证
        validation = self.validator.validate(response["content"])

        # 扣减预算
        self.budget.record_cost(cost_usd)

        # 写入缓存（仅独立查询，多轮对话不缓存）
        if not conversation_id:
            self.cache.store(query, response["content"], response["model"],
                             cost_usd, llm_latency, validation)

        # 更新统计 (线程安全)
        self._inc_stat("ai_calls")
        self._inc_stat("total_cost", cost_usd)

        # 多轮对话：保存到历史
        if conversation_id:
            self._append_conversation(conversation_id, "user", query)
            self._append_conversation(conversation_id, "assistant", response["content"])

        # 构建验证置信度
        v_conf = validation.get("confidence", 0.5)
        result.update({
            "source": "ai",
            "content": response["content"],
            "confidence": v_conf,
            "validation": validation,
            "ai_model": response["model"],
            "cost_usd": cost_usd,
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        })

        return result

    def _call_llm(self, query: str, search_context: Optional[dict] = None,
                   conversation_id: Optional[str] = None) -> dict:
        """调用 LLM 获取回答（含搜索上下文注入 + 结构化输出 + 场景温度 + 多轮对话）。

        Args:
            query: 用户问题
            search_context: 搜索上下文（索引结果、分类等）
            conversation_id: 对话 ID，不为 None 时加载历史
        """
        # ── 1. 搜索上下文注入 ──
        context_block = ""
        if search_context:
            idx_results = search_context.get("results", [])
            idx_categories = search_context.get("categories", [])
            idx_search_type = search_context.get("search_type", "")
            if idx_results:
                ctx_lines = ["以下是知识库中已有的相关规则（供参考，不要重复）："]
                for r in idx_results[:5]:
                    rid = r.get("id", "?")
                    title = r.get("title", "")
                    content = r.get("content", "")[:150]
                    ctx_lines.append(f"  [{rid}] {title}: {content}")
                ctx_lines.append("——参考结束——")
                context_block = "\n".join(ctx_lines)
            if idx_categories:
                cat_str = ", ".join(idx_categories)
                context_block += f"\n搜索范围（分类）: {cat_str}"

        # ── 2. 场景判定 → 温度 + prompt 风格 ──
        is_fallback = bool(search_context and search_context.get("fallback", False))
        if is_fallback:
            # 索引无结果，兜底模式 → 需要创造性 + 明确标注
            temperature = 0.5
            scenario_hint = (
                "注意：知识库中未找到直接匹配的规则，请基于你的知识回答。\n"
                "如答案涉及安全、性能等关键领域，请在开头标注 ⚠️。"
            )
        elif conversation_id:
            # 多轮对话 → 适中温度
            temperature = 0.4
            scenario_hint = "这是对上一个问题的追问，结合对话历史回答。"
        else:
            # 独立查询 → 确定性优先
            temperature = 0.2
            scenario_hint = ""

        # ── 3. 构建 system prompt ──
        system_prompt = (
            "你是一个技术规则知识库助手。回答必须遵循以下规则：\n"
            "1. 精确、具体、可操作，包含代码示例\n"
            "2. 先用一句话概括，再展开说明，最后给出示例\n"
            "3. 如果知识库已有相关规则，引用规则 ID 如 `[规则 id]`\n"
            "4. 如果问题有安全/性能风险，在开头标注 ⚠️\n"
            "5. 不确定时明确说\"我不确定\"，不要编造\n"
        )
        if context_block:
            system_prompt += f"\n{context_block}\n"
        if scenario_hint:
            system_prompt += f"\n{scenario_hint}"

        # ── 4. 构建消息列表（含对话历史） ──
        messages = [{"role": "system", "content": system_prompt}]

        if conversation_id:
            history = self._get_conversation(conversation_id)
            for turn in history[-(self._MAX_CONVERSATION_TURNS - 2):]:
                messages.append(turn)

        messages.append({"role": "user", "content": query})

        return self.provider.chat(messages, max_tokens=self.config.get("max_tokens", 1024),
                                  temperature=temperature)

    def get_budget_status(self) -> dict:
        """预算状态。"""
        return self.budget.get_status()

    def _get_conversation(self, conversation_id: str) -> List[dict]:
        """获取对话历史，不存在时返回空列表。"""
        return self.conversations.get(conversation_id, [])

    def _append_conversation(self, conversation_id: str, role: str, content: str):
        """追加一条对话记录，自动裁剪超出的历史。"""
        if conversation_id not in self.conversations:
            self.conversations[conversation_id] = []
        self.conversations[conversation_id].append({"role": role, "content": content})
        # 裁剪到 MAX_CONVERSATION_TURNS
        if len(self.conversations[conversation_id]) > self._MAX_CONVERSATION_TURNS:
            self.conversations[conversation_id] = self.conversations[conversation_id][-self._MAX_CONVERSATION_TURNS:]

    def clear_conversation(self, conversation_id: str):
        """清除指定对话的历史。"""
        self.conversations.pop(conversation_id, None)

    def get_stats(self) -> dict:
        """运行统计。"""
        return {
            **self.stats,
            "budget": self.budget.get_status(),
            "active_conversations": len(self.conversations),
        }

    def get_provider_info(self) -> dict:
        """当前 Provider 信息。"""
        return {
            "provider": self.config.get("provider", "claude"),
            "model": self.config.get("model", "claude-sonnet-4-6"),
            "enabled": self.is_enabled(),
        }
