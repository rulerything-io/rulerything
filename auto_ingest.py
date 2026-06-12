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
AutoIngest — 自动规则提炼引擎（Phase C）

核心机制：
  - 草稿生成：从 AI 问答对提炼结构化规则，批量处理降成本
  - 双路去重：标题 TF-IDF + 内容 SHA256，自适应阈值
  - 置信度调整：反馈驱动 + 保护期 + 沉默衰减
  - 速率限制：session 上限 + 日上限

Everything 原则：
  - 提炼失败不影响 AI 查询响应
  - 去重防知识库膨胀
  - 可独立开关

用法:
    ingester = AutoIngest(storage, ai_bridge, config)
    ingester.enqueue(query, response, v_result)
    ingester.process_pending()
"""

import hashlib
import json
import queue
import threading
import time
from datetime import datetime, timedelta, date
from typing import Any, Dict, List, Optional, Tuple

from rule import Rule


def _iso_now() -> str:
    return datetime.now().isoformat()


# ── 草稿生成 ────────────────────────────────────────────

class DraftGenerator:
    """从 AI 问答对生成规则草稿（调用 LLM 批量提炼）。

    流程：
      1. 积累待处理问答对（批量 ≥ 3 或队列时间 ≥ 60s）
      2. 一次性发送给低成本 LLM 生成 N 条草稿
      3. 解析 JSON 响应 → 质量校验 → 返回草稿列表
    """

    def __init__(self, storage, ai_bridge, config: dict):
        self.storage = storage
        self.ai_bridge = ai_bridge
        self.max_draft_length = config.get("max_draft_content_length", 500)

    def generate_batch(self, qa_pairs: List[Tuple[str, str, str]]) -> List[Optional[dict]]:
        """批量生成规则草稿。

        Args:
            qa_pairs: [(query, response, validation_result), ...]

        Returns:
            草稿 dict 列表（校验失败的为 None），长度与输入一致
        """
        if not qa_pairs:
            return []

        # 获取已有分类
        existing_cats = self._get_relevant_categories(qa_pairs[0][0])

        # 构建批量 prompt
        items_text = "\n".join(
            f"=== 第{i + 1}条 ===\n问题: {q}\n回答: {a[:800]}"
            for i, (q, a, _) in enumerate(qa_pairs)
        )

        prompt = (
            "从以下技术问答对中提炼可复用的规则。\n\n"
            f"可选分类: {', '.join(existing_cats)}\n\n"
            f"{items_text}\n\n"
            "对每条问答，返回一个 JSON 对象（不要额外说明）：\n"
            "{\n"
            '  "title": "20字以内的概括标题",\n'
            '  "content": "50-300字的具体说明和最佳实践",\n'
            '  "category": "从可选分类中选择最匹配的",\n'
            '  "tags": ["tag1", "tag2", "tag3"]\n'
            "}\n"
            "将所有 JSON 对象放入一个数组返回：\n"
            '[{...}, {...}, ...]'
        )

        # 用低成本模型（haiku 或 equivalent）
        try:
            response = self._call_cheap_llm(prompt)
        except (RuntimeError, NotImplementedError, AttributeError, TypeError):
            # LLM 不可用（如 use_parent_ai 模式），降级为本地提取
            return [self._local_extract(q, a) for q, a, _ in qa_pairs]

        drafts = self._parse_batch_response(response["content"], len(qa_pairs))
        return [self._validate(d) for d in drafts]

    def _local_extract(self, query: str, response: str) -> Optional[dict]:
        """无 LLM 时本地提取规则草稿（use_parent_ai 模式降级方案）。"""
        resp = response.strip()
        if not resp or len(resp) < 20:
            return None

        # 用回答第一句作标题（截断 ≤50 字）
        title = resp.split("。")[0].split("\n")[0]
        if len(title) > 50:
            title = title[:50]
        if len(title) < 4:
            title = query[:50]

        # 回答全文作内容（截断）
        content = resp[:self.max_draft_length * 4]

        # 基于内容关键词频率检测分类（不依赖分类名本身的字符串匹配）
        category = self._detect_category_by_content(query + " " + resp)

        # 从 query 和 content 抽取关键词作标签
        stop_words = {"的", "了", "在", "是", "我", "有", "和", "就", "不", "人",
                       "都", "一", "一个", "上", "也", "很", "到", "说", "要",
                       "去", "你", "会", "着", "没有", "看", "好", "自己", "这",
                       "pending", "query_id"}
        text = f"{query} {resp}"
        for ch in "，。！？、；：""''（）【】《》/\\,.:;!?\"'()[]{}":
            text = text.replace(ch, " ")
        words = []
        for word in text.split():
            word = word.strip().lower()
            # 过滤 query_id 模式、pending 前缀、纯数字、过短词
            if (len(word) >= 3 and word not in stop_words
                    and not word.startswith("q_2026")
                    and not word.replace(".", "").isdigit()):
                words.append(word)

        # 过滤包含 query_id 特征的 token
        words = [w for w in words if not (w.startswith("q_2") and len(w) > 15)]
        from collections import Counter
        tags = [w for w, _ in Counter(words).most_common(5)]

        draft = {
            "title": title,
            "content": content,
            "category": category,
            "tags": tags,
        }
        return self._validate(draft)

    def _detect_category_by_content(self, text: str) -> str:
        """基于已有规则内容的关键词重合度判断最佳分类。

        构建 分类→词语集 映射（来自该分类下所有规则的 title+content+tags + 分类名），
        用词语重合数量排序。无匹配则回退 general。
        """
        try:
            rules = self.storage.list()
        except Exception:
            return "general"

        # 构建分类→高频词集（缓存到实例避免重复扫描）
        if not hasattr(self, "_cat_words_cache"):
            cat_words = {}
            for r in rules:
                cat = r.category
                if cat not in cat_words:
                    cat_words[cat] = set()
                # 加入分类名本身（重要：让 "security" 匹配含 security 的文本）
                cat_words[cat].add(cat.lower())
                tokens = f"{r.title} {r.content} {r.tags}".lower().split()
                for t in tokens:
                    t = t.strip("，。！？、；：""''（）【】《》/\\,.:;!?\"'()[]{}")
                    if len(t) >= 3 and not t.replace(".", "").isdigit():
                        cat_words[cat].add(t)
            self._cat_words_cache = cat_words

        probe_words = set()
        for t in text.lower().split():
            t = t.strip("，。！？、；：""''（）【】《》/\\,.:;!?\"'()[]{}")
            if len(t) >= 3 and not t.replace(".", "").isdigit():
                probe_words.add(t)

        best_cat = "general"
        best_score = 0
        for cat, words in self._cat_words_cache.items():
            overlap = len(probe_words & words)
            if overlap > best_score:
                best_score = overlap
                best_cat = cat

        return best_cat

    def generate_single(self, query: str, ai_response: str,
                        validation_result: str) -> Optional[dict]:
        """单条生成（降级方案，批量不可用时）。"""
        return self.generate_batch([(query, ai_response, validation_result)])[0]

    def _get_relevant_categories(self, query: str) -> List[str]:
        """获取与 query 最相关的 ≤5 个分类。"""
        try:
            rules = self.storage.list()
            cats = sorted(set(r.category for r in rules))
        except Exception:
            cats = ["general"]

        if len(cats) <= 5:
            return cats

        # 用关键词匹配前 5 个
        query_lower = query.lower()
        scored = []
        for cat in cats:
            score = 0
            for word in cat.split():
                if word in query_lower:
                    score += 1
            scored.append((score, cat))
        scored.sort(reverse=True)
        return [c for _, c in scored[:5]] + ["general"]

    def _call_cheap_llm(self, prompt: str) -> dict:
        """调用低成本 LLM（haiku 或 provider 的默认模型）。"""
        messages = [
            {"role": "system", "content": "你是一个规则提炼助手。只返回 JSON 数组，不要额外说明。"},
            {"role": "user", "content": prompt},
        ]
        return self.ai_bridge.provider.chat(messages, max_tokens=2048, temperature=0.1)

    def _parse_batch_response(self, content: str, expected: int) -> List[Optional[dict]]:
        """解析 LLM 返回的 JSON 数组。"""
        # 尝试提取 JSON 数组
        content = content.strip()
        start = content.find("[")
        end = content.rfind("]")
        if start != -1 and end > start:
            content = content[start:end + 1]

        try:
            items = json.loads(content)
            if not isinstance(items, list):
                return [None] * expected
            return items
        except (json.JSONDecodeError, TypeError):
            # 重试：告诉 LLM 格式错误
            return [None] * expected

    def _validate(self, draft: Any) -> Optional[dict]:
        """校验草稿质量，归一化为标准格式。"""
        if not isinstance(draft, dict):
            return None

        title = str(draft.get("title", "")).strip()
        content = str(draft.get("content", "")).strip()
        category = str(draft.get("category", "general")).strip()
        tags = draft.get("tags", [])

        if not isinstance(tags, list):
            tags = [str(tags)]

        # 长度校验（最小长度检查 + 超长截断，避免验证上限硬编码导致截断永远不可达）
        if len(title) < 4:
            return None
        if len(content) < 50:
            return None
        if len(title) > self.max_draft_length:
            title = title[:self.max_draft_length]
        if len(content) > self.max_draft_length * 4:
            content = content[:self.max_draft_length * 4]

        # category 校验
        try:
            valid_cats = set(r.category for r in self.storage.list())
            if valid_cats and category not in valid_cats:
                category = "general"
        except Exception:
            pass

        # tags 校验
        tags = [str(t).strip() for t in tags if str(t).strip()][:10]

        # 计算 content_hash
        content_hash = hashlib.sha256(content.lower().encode()).hexdigest()[:16]

        return {
            "title": title,
            "content": content,
            "category": category,
            "tags": tags or ["ai"],
            "content_hash": content_hash,
        }


# ── 双路去重 ────────────────────────────────────────────

class DualDedup:
    """双路去重：标题 TF-IDF 相似度 + 内容哈希精确匹配。

    自适应阈值：
      - 跟踪去重率，动态调整 title_sim_threshold
      - 去重率 > 50% → 放松（+0.05），< 5% → 收紧（-0.05）
    """

    def __init__(self, storage, config: dict):
        self.storage = storage
        self.base_threshold = config.get("title_dedup_threshold", 0.7)
        self.threshold = self.base_threshold
        self._total_checks = 0
        self._dup_count = 0
        # O(1) 内容哈希去重缓存
        self._content_hashes: set = set()
        self._content_hash_to_rule: Dict[str, Tuple[str, str]] = {}
        self._rebuild_hash_cache()

    def _rebuild_hash_cache(self):
        """重建内容哈希缓存（从 storage 加载所有规则到 O(1) 查找集）。"""
        try:
            rules = self.storage.list()
            for r in rules:
                if hasattr(r, "content"):
                    h = self._content_hash(r.content)
                    self._content_hashes.add(h)
                    self._content_hash_to_rule[h] = (r.id, r.title)
        except Exception:
            self._content_hashes = set()
            self._content_hash_to_rule = {}

    def check(self, draft: dict) -> dict:
        """检查草稿是否与现有规则重复。

        Returns:
            {
                "is_duplicate": bool,
                "method": "title_sim" | "content_hash" | None,
                "matched_rule_id": str | None,
                "matched_rule_title": str | None,
                "similarity": float,
            }
        """
        self._total_checks += 1

        # A. 内容哈希精确去重（O(1) 缓存查找）
        content_hash = draft.get("content_hash", "")
        if content_hash and content_hash in self._content_hashes:
            self._dup_count += 1
            self._adapt_threshold()
            matched_id, matched_title = self._content_hash_to_rule.get(content_hash, (None, None))
            return {
                "is_duplicate": True,
                "method": "content_hash",
                "matched_rule_id": matched_id,
                "matched_rule_title": matched_title,
                "similarity": 1.0,
            }

        # B. 标题 TF-IDF 相似度
        title = draft.get("title", "")
        if title:
            try:
                titles = [(r.id, r.title) for r in self.storage.list()]
                best_id, best_title, best_sim = self._find_similar_title(title, titles)
                if best_sim >= self.threshold and best_id:
                    self._dup_count += 1
                    self._adapt_threshold()
                    return {
                        "is_duplicate": True,
                        "method": "title_sim",
                        "matched_rule_id": best_id,
                        "matched_rule_title": best_title,
                        "similarity": round(best_sim, 4),
                    }
            except Exception:
                pass

        return {
            "is_duplicate": False,
            "method": None,
            "matched_rule_id": None,
            "matched_rule_title": None,
            "similarity": 0.0,
        }

    def _find_similar_title(self, title: str, titles: List[Tuple[str, str]]) -> Tuple[Optional[str], Optional[str], float]:
        """找到最相似的标题（Jaccard 相似度）。"""
        import re
        title_tokens = set()
        for word in re.findall(r'[a-z_+#0-9\u4e00-\u9fff]+', title.lower()):
            if len(word) >= 2:
                title_tokens.add(word)

        if not title_tokens:
            return None, None, 0.0

        best_id, best_title, best_sim = None, None, 0.0
        for rid, rtitle in titles:
            rtokens = set()
            for word in re.findall(r'[a-z_+#0-9\u4e00-\u9fff]+', rtitle.lower()):
                if len(word) >= 2:
                    rtokens.add(word)
            if not rtokens:
                continue
            sim = len(title_tokens & rtokens) / max(len(title_tokens | rtokens), 1)
            if sim > best_sim:
                best_sim = sim
                best_id = rid
                best_title = rtitle

        return best_id, best_title, best_sim

    @staticmethod
    def _content_hash(content: str) -> str:
        """计算内容的 SHA256 哈希。"""
        return hashlib.sha256(content.lower().encode()).hexdigest()[:16]

    def _adapt_threshold(self):
        """自适应调整去重阈值。"""
        if self._total_checks < 10:
            return
        rate = self._dup_count / self._total_checks
        if rate > 0.5:
            self.threshold = min(0.9, self.threshold + 0.05)
        elif rate < 0.05:
            self.threshold = max(0.5, self.threshold - 0.05)

    def get_stats(self) -> dict:
        """去重统计。"""
        return {
            "total_checks": self._total_checks,
            "duplicates_found": self._dup_count,
            "current_threshold": round(self.threshold, 3),
            "base_threshold": self.base_threshold,
        }


# ── 置信度调整 ─────────────────────────────────────────

class ConfidenceAdjuster:
    """基于反馈的置信度调整。

    规则:
      - 初始 confidence = 0.5（verifier=ai 的新规则）
      - 正面反馈 +0.05，负面反馈 -0.1
      - 范围 clamp(0.05, 0.95)
      - 30 天保护期：新规则不衰减
      - 保护期后：hit_count > 0 且 30 天无反馈 → ×0.9 衰减
      - confidence ≥ 0.7 且反馈率 > 80% → 升级 verifier = "ai_verified"
      - confidence ≤ 0.1 → 自动标记过期
    """

    def __init__(self, storage):
        self.storage = storage
        self.protection_days = 30
        self.decay_days = 30

    def record_feedback(self, rule_id: str, positive: bool) -> dict:
        """记录反馈并更新置信度。

        Returns:
            {"rule_id": str, "old_confidence": float, "new_confidence": float,
             "action": "updated" | "expired" | "promoted"}
        """
        rule = self.storage.get(rule_id)
        if not rule:
            return {"rule_id": rule_id, "error": "规则不存在"}

        old_conf = rule.confidence
        new_conf = old_conf + (0.05 if positive else -0.1)
        new_conf = max(0.05, min(0.95, new_conf))

        action = "updated"
        evolution_entry = f"ai_feedback: {'positive' if positive else 'negative'} ({old_conf:.2f} → {new_conf:.2f})"

        # ≤ 0.1 → 过期
        if new_conf <= 0.1:
            self.storage.update(
                rule_id,
                confidence=new_conf,
                expires_at=_iso_now(),
                evolution_log=rule.evolution_log + [f"auto_expired: 置信度降至 {new_conf:.2f}"],
            )
            action = "expired"
        else:
            update_kw = {
                "confidence": new_conf,
                "evolution_log": rule.evolution_log + [evolution_entry],
            }

            # ≥ 0.7 且反馈率 > 80% → 升级
            if new_conf >= 0.7:
                try:
                    stats = self.storage.get_ai_feedback_stats(rule_id)
                    if stats["total"] >= 3 and stats["ratio"] >= 0.8:
                        update_kw["verifier"] = "ai_verified"
                        action = "promoted"
                except Exception:
                    pass

            self.storage.update(rule_id, **update_kw)

        return {
            "rule_id": rule_id,
            "old_confidence": old_conf,
            "new_confidence": round(new_conf, 4),
            "action": action,
        }

    def decay_check(self) -> List[str]:
        """对沉默规则执行衰减。

        条件：
          - 创建超过 protection_days
          - hit_count > 0（至少被展示过）
          - 30 天内无反馈
          - verifier == "ai"

        Returns:
            被衰减的 rule_id 列表
        """
        decayed = []
        try:
            rules = self.storage.list()
            now = datetime.now()

            for rule in rules:
                if rule.verifier not in ("ai", "ai_verified"):
                    continue
                if not rule.created_at:
                    continue

                # 处理 created_at 可能是 datetime 或 str
                created = rule.created_at
                if isinstance(created, str):
                    try:
                        created = datetime.fromisoformat(created)
                    except (ValueError, TypeError):
                        continue
                if not isinstance(created, datetime):
                    continue

                # 保护期检查
                days_exist = (now - created).days
                if days_exist < self.protection_days:
                    continue

                # 必须被展示过
                if rule.hit_count == 0:
                    continue

                # 30 天内有反馈？
                try:
                    feedback = self.storage.get_ai_feedback(rule.id)
                    if feedback:
                        last_fb = max(datetime.fromisoformat(f["timestamp"]) for f in feedback if f.get("timestamp"))
                        if (now - last_fb).days < self.decay_days:
                            continue
                except Exception:
                    pass

                # 执行衰减
                new_conf = max(0.05, rule.confidence * 0.9)
                if new_conf <= 0.1:
                    self.storage.update(
                        rule.id,
                        confidence=new_conf,
                        expires_at=_iso_now(),
                        evolution_log=rule.evolution_log + [f"auto_expired: 沉默衰减至 {new_conf:.2f}"],
                    )
                else:
                    self.storage.update(
                        rule.id,
                        confidence=new_conf,
                        evolution_log=rule.evolution_log + [f"decay: 沉默衰减 ({rule.confidence:.2f} → {new_conf:.2f})"],
                    )
                decayed.append(rule.id)

        except Exception:
            pass

        return decayed

    def promote_check(self) -> List[str]:
        """检查可升级为 ai_verified 的规则。"""
        promoted = []
        try:
            rules = self.storage.list()
            for rule in rules:
                if rule.verifier != "ai":
                    continue
                if rule.confidence < 0.7:
                    continue
                try:
                    stats = self.storage.get_ai_feedback_stats(rule.id)
                    if stats["total"] >= 3 and stats["ratio"] >= 0.8:
                        self.storage.update(
                            rule.id,
                            verifier="ai_verified",
                            evolution_log=rule.evolution_log + ["promoted: 反馈达标升级为 ai_verified"],
                        )
                        promoted.append(rule.id)
                except Exception:
                    pass
        except Exception:
            pass
        return promoted


# ── 统一入口 ────────────────────────────────────────────

class AutoIngest:
    """自动规则提炼统一入口。

    流程:
      [API 响应] → enqueue() → [内存队列]
          ↓ (management_loop 定时)
      process_pending() → 批量取出 QA 对
          ↓
      DraftGenerator.generate_batch() → 草稿列表
          ↓ (逐条)
      DualDedup.check() → 重复? → 丢弃并记录
          ↓ 不重复
      创建 Rule → storage.add() → audit_log → stats++
    """

    def __init__(self, storage, ai_bridge, config: dict):
        self.storage = storage
        self.ai_bridge = ai_bridge
        self.config = config

        self.max_per_session = config.get("max_new_rules_per_session", 10)
        self.max_per_day = config.get("max_new_rules_per_day", 50)
        self.batch_interval_seconds = config.get("batch_interval_seconds", 30)

        # 子模块
        self.draft_generator = DraftGenerator(storage, ai_bridge, config)
        self.dedup = DualDedup(storage, config)
        self.confidence_adjuster = ConfidenceAdjuster(storage)

        # 速率控制
        self._session_count = 0
        self._daily_count = 0
        self._today = date.today()
        self._last_batch_time = 0.0

        # 队列
        self._queue: queue.Queue = queue.Queue()
        self._pending_count = 0
        self._lock = threading.Lock()

        # 统计
        self.stats = {
            "total_ingested": 0,
            "total_duplicates": 0,
            "total_skipped": 0,
            "total_errors": 0,
            "current_queue_size": 0,
        }

    def enqueue(self, query: str, ai_response: str, validation_result: str):
        """将问答对加入待处理队列。"""
        self._queue.put_nowait((query, ai_response, validation_result))
        with self._lock:
            self._pending_count += 1

    def process_pending(self) -> List[str]:
        """处理队列中的待提炼项（批量）。

        Returns:
            本次创建的 rule_id 列表
        """
        # 检查日限
        self._check_daily_reset()
        if self._daily_count >= self.max_per_day:
            return []

        # 收集队列中的待处理项
        batch = []
        while not self._queue.empty() and len(batch) < 5:
            try:
                batch.append(self._queue.get_nowait())
                with self._lock:
                    self._pending_count -= 1
            except queue.Empty:
                break

        if not batch:
            return []

        # 过滤：仅处理 CONSISTENT / PARTIAL
        valid = [(q, a, v) for q, a, v in batch
                 if v in ("consistent", "partial")]

        if not valid:
            return []

        # Session 上限检查
        remaining = self.max_per_session - self._session_count
        if remaining <= 0:
            self.stats["total_skipped"] += len(valid)
            for q, _, _ in valid:
                self.storage.log_ingestion(query=q, status="skipped",
                                           error_message="session_limit")
            return []

        valid = valid[:remaining]

        # 批量生成草稿
        drafts = self.draft_generator.generate_batch(valid)

        created_ids = []
        for (q, a, v), draft in zip(valid, drafts):
            if draft is None:
                self.stats["total_errors"] += 1
                self.storage.log_ingestion(query=q, status="failed",
                                           error_message="draft_generation_failed")
                continue

            # 去重检查
            dedup_result = self.dedup.check(draft)
            if dedup_result["is_duplicate"]:
                self.stats["total_duplicates"] += 1
                self.storage.log_ingestion(
                    query=q, status="duplicate",
                    title=draft["title"], category=draft["category"],
                    dedup_method=dedup_result["method"],
                    matched_rule_id=dedup_result["matched_rule_id"],
                )
                continue

            # 创建规则
            rule_id = self._create_rule(draft)
            if rule_id:
                self._session_count += 1
                self._daily_count += 1
                created_ids.append(rule_id)
                self.storage.log_ingestion(
                    query=q, rule_id=rule_id,
                    title=draft["title"], category=draft["category"],
                    status="created",
                )
            else:
                self.stats["total_errors"] += 1
                self.storage.log_ingestion(
                    query=q, status="failed",
                    title=draft["title"],
                    error_message="rule_creation_failed",
                )

        self.stats["total_ingested"] += len(created_ids)
        with self._lock:
            self.stats["current_queue_size"] = self._pending_count

        return created_ids

    def _create_rule(self, draft: dict) -> Optional[str]:
        """根据草稿创建规则。"""
        from datetime import datetime
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        hash_suffix = draft.get("content_hash", "0000")[:8]
        rule_id = f"auto_{draft['category']}_{ts}_{hash_suffix}"

        rule = Rule(
            id=rule_id,
            title=draft["title"],
            content=draft["content"],
            category=draft["category"],
            tags=draft.get("tags", ["ai"]),
            confidence=0.5,
            verifier="ai",
            evolution_log=["ai_ingested: 来自 AI 问答提炼"],
        )

        try:
            ok, msg = self.storage.add(rule)
            if ok:
                self.storage.log_audit(
                    action="ai_ingest",
                    module="auto_ingest",
                    target=rule_id,
                    result="success",
                )
                return rule_id
            else:
                return None
        except Exception:
            return None

    def _check_daily_reset(self):
        """检测日期变更，重置日计数。"""
        today = date.today()
        if today > self._today:
            self._daily_count = 0
            self._session_count = 0
            self._today = today

    def reset_session(self):
        """手动重置 session 计数。"""
        self._session_count = 0

    def get_stats(self) -> dict:
        """运行统计。"""
        return {
            **self.stats,
            "session_count": self._session_count,
            "daily_count": self._daily_count,
            "daily_limit": self.max_per_day,
            "session_limit": self.max_per_session,
            "dedup": self.dedup.get_stats(),
        }
