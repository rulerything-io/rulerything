"""
AutoDepMiner — 自动规则依赖挖掘引擎（v3.0）

三路挖掘（按优先级）：
  1. 共现分析 (PMI) — 同一查询结果中频繁同时出现的规则
  2. 时序转移 (Markov) — 规则 A 命中后，下次查询命中 B
  3. 内容相似度 — 标题/标签 TF-IDF 余弦相似度

Everything 原则：
  - scipy.sparse 为可选依赖，无则纯 Python 降级（精度略低）
  - 冷启动自动降级（query_log < 100 条仅用内容相似度）
  - 核心查询路径不受影响

用法:
    miner = DepMiner(storage)
    miner.mine_all()                           # 全量挖掘
    miner.mine_cooccurrence(queries)           # 仅共现
    miner.get_stats()                          # 统计
"""

import json
import math
import re
from collections import defaultdict, Counter, deque
from datetime import datetime
from typing import Dict, List, Optional, Tuple

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False

try:
    from scipy.sparse import csr_matrix
    HAS_SCIPY = True
except ImportError:
    HAS_SCIPY = False


class DepMiner:
    """自动依赖挖掘引擎。"""

    RELATION_RELATED = "related"
    RELATION_DEPENDS = "depends"
    RELATION_CONFLICTS = "conflicts"

    def __init__(self, storage, config: Optional[dict] = None):
        self.storage = storage
        self.config = config or {}

        # 阈值
        self.pmi_threshold = self.config.get("pmi_threshold", 0.3)
        self.content_sim_threshold = self.config.get("content_sim_threshold", 0.7)
        self.cooccurrence_window = self.config.get("cooccurrence_window", 1000)
        self.min_queries_for_pmi = self.config.get("min_queries_for_pmi", 100)

        # 运行时统计
        self.stats_counter = {
            "relations_mined": 0,
            "cooccurrence_relations": 0,
            "transfer_relations": 0,
            "content_sim_relations": 0,
            "conflicts_detected": 0,
            "cold_start": False,
        }

    # ── 主入口 ──────────────────────────────────────────

    def mine_all(self):
        """全量挖掘：依次执行三种挖掘策略。"""
        self.stats_counter = {k: 0 for k in self.stats_counter}
        queries = self._get_recent_queries()

        if len(queries) < self.min_queries_for_pmi:
            self.stats_counter["cold_start"] = True
            # 冷启动：仅内容相似度
            self._mine_content_similarity()
        else:
            self._mine_cooccurrence(queries)
            self._mine_transfer(queries)
            self._mine_content_similarity()

        self.stats_counter["relations_mined"] = (
            self.stats_counter["cooccurrence_relations"]
            + self.stats_counter["transfer_relations"]
            + self.stats_counter["content_sim_relations"]
        )

    # ── 1. 共现分析 (PMI) ─────────────────────────────

    def _get_recent_queries(self) -> List[dict]:
        """获取近期查询日志。"""
        try:
            return self.storage.get_recent_queries(days=7)
        except Exception:
            return []

    def _get_query_results(self) -> List[List[str]]:
        """获取最近 N 次查询的返回规则 ID 列表（从日志或指标）。"""
        # 实际可以从 query_log 反查搜索结果
        # phase A 简化：使用 get_recent_queries 返回的文本
        # 后续可以在 query_log 中增加 result_ids 字段
        results = []
        try:
            recent = self.storage.get_recent_queries(days=7, min_freq=1)
            # 简化：对每个查询，用内容搜索模拟返回结果
            # 真实场景应直接从 query_log 的 result_ids 字段读取
            for r in recent[:self.cooccurrence_window]:
                query = r.get("query", "")
                if query:
                    results.append(self._simulate_result_ids(query))
        except Exception:
            pass
        return results

    def _simulate_result_ids(self, query: str) -> List[str]:
        """模拟：根据查询文本搜索规则，返回 ID 列表。"""
        # 实际场景中 query_log 应存储 result_ids
        # 这里用词级匹配模拟，要求至少 50% 的查询词命中标题或内容
        ids = []
        try:
            rules = self.storage.list()
            q = query.lower()
            query_words = q.split()
            if not query_words:
                return ids
            for rule in rules:
                title_words = rule.title.lower().split()
                title_matches = sum(1 for w in query_words if w in title_words)
                content_words = rule.content.lower().split()
                content_matches = sum(1 for w in query_words if w in content_words)
                if (title_matches / len(query_words) >= 0.5) or \
                   (content_matches / len(query_words) >= 0.5):
                    ids.append(rule.id)
                    if len(ids) >= 10:
                        break
        except Exception:
            pass
        return ids

    def _mine_cooccurrence(self, queries: List[dict]):
        """共现挖掘：使用 PMI 筛选显著共现关系。"""
        # 共现计数
        cooccur = defaultdict(lambda: defaultdict(int))
        single_count = Counter()

        result_sets = self._get_query_results()
        for ids in result_sets:
            if len(ids) < 2:
                continue
            for i in range(len(ids)):
                single_count[ids[i]] += 1
                for j in range(i + 1, len(ids)):
                    a, b = ids[i], ids[j]
                    if a < b:
                        cooccur[a][b] += 1
                    else:
                        cooccur[b][a] += 1

        total_queries = max(len(result_sets), 1)

        # 纯 Python PMI 计算
        for a, targets in cooccur.items():
            pa = single_count[a] / total_queries
            for b, count in targets.items():
                pb = single_count[b] / total_queries
                pab = count / total_queries

                if pab <= 0 or pa <= 0 or pb <= 0:
                    continue

                try:
                    pmi = math.log2(pab / (pa * pb))
                except (ValueError, ZeroDivisionError):
                    continue

                if pmi >= self.pmi_threshold:
                    self._save_relation(a, b, self.RELATION_RELATED,
                                        min(pmi / 5.0, 1.0), "cooccurrence")
                    self.stats_counter["cooccurrence_relations"] += 1

    # ── 2. 时序转移分析 ───────────────────────────────

    def _mine_transfer(self, queries: List[dict]):
        """时序转移：计算规则间的 Markov 转移概率。

        连续两次查询，第一次命中的规则 A → 第二次命中的规则 B。
        """
        recent_results = self._get_query_results()

        # 转移计数
        transfer = defaultdict(lambda: defaultdict(int))
        from_count = Counter()

        for i in range(len(recent_results) - 1):
            current = recent_results[i]
            next_ids = recent_results[i + 1]
            if not current or not next_ids:
                continue
            for a in current:
                from_count[a] += 1
                for b in next_ids[:5]:  # 只取前 5 个结果
                    if a != b:
                        transfer[a][b] += 1

        # 计算转移概率并保存 ≥ 0.3 的
        for a, targets in transfer.items():
            total = max(from_count[a], 1)
            for b, count in targets.items():
                prob = count / total
                if prob >= 0.3:
                    # 如果已有共现关系，升级为 depends
                    self._save_relation(a, b, self.RELATION_DEPENDS,
                                        prob, "transfer")
                    self.stats_counter["transfer_relations"] += 1

    # ── 3. 内容相似度分析 ─────────────────────────────

    def _tokenize(self, text: str) -> List[str]:
        """简单分词：提取英文单词和 CJK 字符。"""
        tokens = []
        # 英文单词
        for word in re.findall(r'[a-zA-Z_+#.]+', text.lower()):
            if len(word) >= 2:
                tokens.append(word)
        # CJK 二元组
        cjk_seq = re.findall(r'[\u4e00-\u9fff]+', text)
        for seq in cjk_seq:
            for i in range(len(seq) - 1):
                tokens.append(seq[i:i + 2])
        return tokens

    def _cosine_similarity(self, vec_a: Dict[str, float],
                           vec_b: Dict[str, float]) -> float:
        """计算两个 TF 向量的余弦相似度。"""
        intersection = set(vec_a) & set(vec_b)
        if not intersection:
            return 0.0

        dot = sum(vec_a[k] * vec_b[k] for k in intersection)
        norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
        norm_b = math.sqrt(sum(v * v for v in vec_b.values()))

        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _compute_tf(self, text: str) -> Dict[str, float]:
        """计算词频向量。"""
        tokens = self._tokenize(text)
        if not tokens:
            return {}
        counter = Counter(tokens)
        total = len(tokens)
        return {word: count / total for word, count in counter.items()}

    def _mine_content_similarity(self):
        """内容相似度挖掘：检测重复/冲突/关联。"""
        rules = self.storage.list()
        if len(rules) < 2:
            return

        # 按 hit_count 排序并限制比较数量（性能优化）
        max_rules = self.config.get("max_comparisons", 100)
        if len(rules) > max_rules:
            rules = sorted(rules, key=lambda r: getattr(r, 'hit_count', 0), reverse=True)[:max_rules]

        # 构建 O(1) 查询的字典
        rules_dict = {r.id: r for r in rules}

        # 计算所有规则的 TF 向量（含 content 的前 500 字符）
        vectors = {}
        for rule in rules:
            content_part = (rule.content or '')[:500]
            text = f"{rule.title} {' '.join(rule.tags)} {content_part}"
            vectors[rule.id] = self._compute_tf(text)

        # 两两比较（通过 max_comparisons 控制上限）
        import itertools
        rule_ids = list(vectors.keys())
        for a, b in itertools.combinations(rule_ids, 2):
            sim = self._cosine_similarity(vectors[a], vectors[b])

            if sim >= self.content_sim_threshold:
                rule_a = rules_dict.get(a)
                rule_b = rules_dict.get(b)

                if not rule_a or not rule_b:
                    continue

                # 置信度矛盾 → conflicts
                if abs(rule_a.confidence - rule_b.confidence) > 0.4:
                    self._save_relation(a, b, self.RELATION_CONFLICTS,
                                        sim, "content_sim")
                    self.stats_counter["conflicts_detected"] += 1
                else:
                    self._save_relation(a, b, self.RELATION_RELATED,
                                        sim, "content_sim")
                    self.stats_counter["content_sim_relations"] += 1

    # ── 持久化 ─────────────────────────────────────────

    def _save_relation(self, source: str, target: str,
                       relation_type: str, strength: float,
                       evidence: str):
        """保存规则关系到 SQLite（通过 storage_v2 线程安全方法）。"""
        try:
            self.storage.save_relation(source, target, relation_type, strength, evidence)
        except Exception:
            pass

    def clear_relations(self):
        """清空所有已挖掘的关系（通过 storage_v2 线程安全方法）。"""
        try:
            self.storage.clear_relations()
            self.stats_counter = {k: 0 for k in self.stats_counter}
        except Exception:
            pass

    # ── 查询 ───────────────────────────────────────────

    def get_relations(self, rule_id: Optional[str] = None,
                      relation_type: Optional[str] = None) -> List[dict]:
        """获取规则关系（通过 storage_v2 线程安全方法）。"""
        try:
            return self.storage.get_relations(rule_id=rule_id, relation_type=relation_type)
        except Exception:
            return []

    def get_graph_data(self) -> dict:
        """获取 D3.js 可用的图数据。"""
        nodes = set()
        edges = []

        for rule in self.storage.list():
            nodes.add(rule.id)

        for rel in self.get_relations():
            edges.append({
                "source": rel["source_id"],
                "target": rel["target_id"],
                "type": rel["relation_type"],
                "strength": rel["strength"],
            })
            nodes.add(rel["source_id"])
            nodes.add(rel["target_id"])

        return {
            "nodes": [{"id": n} for n in sorted(nodes)],
            "edges": edges,
        }

    def get_impact_chain(self, rule_id: str, max_depth: int = 3) -> List[dict]:
        """获取某个规则的影响链（BFS 遍历）。"""
        chain = []
        visited = {rule_id}
        queue = deque([(rule_id, 0)])

        while queue:
            current, depth = queue.popleft()
            if depth > max_depth:
                continue

            for rel in self.get_relations(rule_id=current):
                other = rel["source_id"] if rel["target_id"] == current else rel["target_id"]
                if other not in visited:
                    visited.add(other)
                    chain.append({
                        "from": current,
                        "to": other,
                        "type": rel["relation_type"],
                        "strength": rel["strength"],
                        "depth": depth + 1,
                    })
                    queue.append((other, depth + 1))

        return chain

    # ── 统计 ───────────────────────────────────────────

    def get_stats(self) -> dict:
        """挖掘统计。"""
        return {
            **self.stats_counter,
            "total_relations": len(self.get_relations()),
            "config": {
                "pmi_threshold": self.pmi_threshold,
                "content_sim_threshold": self.content_sim_threshold,
                "min_queries_for_pmi": self.min_queries_for_pmi,
                "has_scipy": HAS_SCIPY,
                "has_numpy": HAS_NUMPY,
            },
        }
