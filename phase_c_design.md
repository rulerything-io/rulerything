# Phase C: AI 桥接 + 自动学习 — 完整设计

> 目标：AI 增强查询（低置信度衰减时自动补位）+ 从问答中自动提炼新规则。
> Everything 原则：AI 模块默认关闭，不影响核心查询路径；LLM 不可用时平滑降级。

---

## 目录

- [C1. ai_bridge.py — AI 桥接层](#c1-ai_bridgepy--ai-桥接层)
- [C2. auto_ingest.py — 自动规则提炼](#c2-auto_ingestpy--自动规则提炼)
- [C3. 存储层变更](#c3-存储层变更)
- [C4. main.py 集成](#c4-mainpy-集成)
- [C5. config.py 配置](#c5-configpy-配置)
- [C6. 完整工作流示例](#c6-完整工作流示例)
- [C7. 降级策略矩阵](#c7-降级策略矩阵)

---

## C1. ai_bridge.py — AI 桥接层

### 职责

1. 封装 LLM 调用（多 provider 抽象），统一接口
2. 预算控制（每日硬上限 + 逐次跟踪）
3. AI 回答验证（与系统规则库交叉检验）
4. 查询增强：当系统搜索结果置信度不足时，用 AI 补位
5. 调用缓存：相同问题在窗口期内不重复调用 LLM

### 类设计

```
ai_bridge.py
├── AIBudget              # 预算控制
├── AIProvider            # LLM 提供者抽象基类
│   ├── ClaudeProvider    # Anthropic Claude
│   ├── OpenAIProvider    # OpenAI 兼容
│   └── LocalProvider     # 本地模型 (ollama/vLLM)
├── AIValidator           # AI 回答验证器
├── AICache               # 调用缓存
└── AIBridge              # 统一入口
```

### AIBudget

```python
class AIBudget:
    """
    每日预算控制，硬上限。

    逻辑：
      - 每日重置：date.today() 变化时自动重置 cost_today = 0
      - 硬上限：cost_today >= daily_limit → allow_call() 返回 False
      - 逐次扣减：每次调用前预扣估算值，调用后按实际扣减
      - 持久化：cost_today 写入 config_runtime 表，进程重启不丢失
      - 告警：超过 80% 阈值时写 audit_log

    配置：
      daily_limit_usd: 5.0    # 每日预算上限
      warn_threshold: 0.8     # 告警阈值（占 daily_limit 比例）
      per_call_limit: 0.5     # 单次调用上限（防止意外大额调用）
    """
```

### AIProvider 抽象

```python
class AIProvider(ABC):
    """LLM 提供者抽象。"""

    @abstractmethod
    def chat(self, messages: list[dict], max_tokens: int = 1024,
             temperature: float = 0.3) -> dict:
        """
        Returns:
            {
                "content": str,
                "model": str,
                "usage": {"prompt_tokens": int, "completion_tokens": int},
                "cost_usd": float,
            }
        """
        ...

    @abstractmethod
    def estimate_cost(self, prompt_tokens: int, completion_tokens: int) -> float:
        """估算一次调用的 USD 成本。"""
        ...

    @classmethod
    def from_config(cls, config: dict) -> "AIProvider":
        """从配置创建 provider 实例。"""
        ...
```

#### ClaudeProvider

```python
class ClaudeProvider(AIProvider):
    """Anthropic Claude API。

    API:
      POST https://api.anthropic.com/v1/messages
      Headers: x-api-key, anthropic-version=2023-06-01

    成本模型 (claude-sonnet-4-6):
      input: $3.00 / M tokens
      output: $15.00 / M tokens

    环境变量: ANTHROPIC_API_KEY
    配置:
      ai_bridge.model: claude-sonnet-4-6
      ai_bridge.endpoint: https://api.anthropic.com/v1/messages
    """

    MODEL_COSTS = {
        "claude-sonnet-4-6": {"input": 3.0, "output": 15.0},
        "claude-haiku-4-5": {"input": 1.0, "output": 5.0},
    }
```

#### OpenAIProvider

```python
class OpenAIProvider(AIProvider):
    """OpenAI 兼容 API（OpenAI / Azure / vLLM）。

    环境变量: OPENAI_API_KEY
    配置:
      ai_bridge.model: gpt-4o-mini
      ai_bridge.endpoint: https://api.openai.com/v1/chat/completions
    """

    MODEL_COSTS = {
        "gpt-4o": {"input": 2.5, "output": 10.0},
        "gpt-4o-mini": {"input": 0.15, "output": 0.6},
    }
```

#### LocalProvider

```python
class LocalProvider(AIProvider):
    """本地模型 (ollama/vLLM)。

    成本固定为 0，适合开发/内网部署。

    配置:
      ai_bridge.endpoint: http://localhost:11434/api/chat
      ai_bridge.model: qwen2.5:7b
    """

    def estimate_cost(self, prompt_tokens, completion_tokens) -> float:
        return 0.0  # 本地模型零成本
```

### AIValidator

```python
class AIValidationResult(Enum):
    CONSISTENT   = "consistent"    # 与系统知识一致 → 可信
    PARTIAL      = "partial"       # 部分一致 → 部分可信
    UNVERIFIABLE = "unverifiable"  # 系统无相关知识 → 无法验证
    CONTRADICT   = "contradict"    # 与系统知识矛盾 → 不可信


class AIValidator:
    """
    用系统已有规则库验证 AI 回答。

    流程:
      1. 提取 AI 回答中的关键断言（句子级）
      2. 对每个断言，在规则库中搜索相关规则
         - 用 gap_detector 的 TF-IDF + cosine similarity（复用代码）
         - 匹配到规则 → 比对断言与规则内容
      3. 汇总所有断言的验证结果

    判定规则:
      - 所有断言均匹配 & 无矛盾 → CONSISTENT
      - 部分匹配 & 无矛盾 → PARTIAL
      - 无相关规则 → UNVERIFIABLE
      - 任一断言与规则矛盾 → CONTRADICT

    输出:
      {
          "result": AIValidationResult,
          "matched_rules": [rule_id, ...],
          "contradictions": [{"assertion": str, "rule_id": str, "rule_content": str}, ...],
          "confidence": float,  # 验证置信度
      }
    """
```

### AICache

```python
class AICache:
    """
    AI 调用缓存（DB 持久化，进程重启后可用）。

    表结构 (ai_cache):
      query_hash  TEXT PRIMARY KEY,  # SHA256(query)
      query       TEXT NOT NULL,
      response    TEXT NOT NULL,
      model       TEXT NOT NULL,
      cost_usd    REAL NOT NULL DEFAULT 0,
      created_at  TEXT NOT NULL,
      hit_count   INTEGER DEFAULT 1

    策略:
      - TTL: 24 小时过期
      - max_entries: 5000（超限淘汰 LRU）
      - 缓存命中不计入预算

    SQL:
      SELECT response, cost_usd FROM ai_cache
      WHERE query_hash = ? AND created_at > datetime('now', '-1 day')
    """
```

### AIBridge（统一入口）

```python
class AIBridge:
    """
    AI 桥接层统一入口。

    查询增强流程（_enhance_query）:
      [用户查询]
          ↓
      ① storage.search(query) — 系统检索
          ↓
      ② max_confidence >= threshold? ──→ 直接返回系统结果（不调用 AI）
          ↓ 否
      ③ AIBudget.allow_call()? ──→ 否 → 返回系统结果 + 标注"AI 预算已耗尽"
          ↓ 是
      ④ AICache.lookup(query)? ──→ 命中 → 返回缓存结果（不计费）
          ↓ 未命中
      ⑤ AIProvider.chat() — 调用 LLM
          ↓
      ⑥ AIValidator.validate() — 用规则库交叉验证
          ↓
      ⑦ AIBudget.record_cost() — 扣减预算
          ↓
      ⑧ AICache.store() — 写入缓存
          ↓
      ⑨ 返回增强结果（含验证标签）

    返回格式:
      {
          "query": str,
          "source": "ai" | "system" | "cache",
          "content": str,
          "confidence": float,
          "validation": {
              "result": "consistent" | "partial" | "unverifiable" | "contradict",
              "matched_rules": [str, ...],
              "contradictions": [...],
          },
          "ai_model": str,
          "cost_usd": float,
          "latency_ms": float,
      }

    关键方法:
      - enhance_query(query: str, threshold: float = 0.6) -> dict
      - get_budget_status() -> dict
      - get_stats() -> dict
    """
```

### 结构化 Prompt 设计

```
System:
你是一个技术规则知识库助手。你的任务是回答技术问题，遵循以下原则：

1. 答案应精确、具体、可操作，包含代码示例
2. 如果问题涉及特定语言/框架，给出该语言/框架的最佳实践
3. 回答格式：先用一句话概括，然后展开说明，最后给出示例（如适用）
4. 如果问题有安全风险，在回答开头标注 ⚠️

User: {用户问题}
```

---

## C2. auto_ingest.py — 自动规则提炼

### 职责

1. 从 AI 问答对中提取可复用的知识点
2. 生成结构化规则草稿（title/content/category/tags）
3. 双路去重（标题 TF-IDF 相似度 + 内容哈希精确匹配）
4. 自动入库（verifier=ai, confidence=0.5）
5. 用户反馈驱动置信度调整
6. 速率限制（每次 session 最多 N 条，每日最多 M 条）

### 类设计

```
auto_ingest.py
├── DraftGenerator        # 规则草稿生成（调用 LLM）
├── DualDedup             # 双路去重
├── ConfidenceAdjuster    # 置信度调整（基于反馈）
└── AutoIngest            # 统一入口
```

### DraftGenerator

```python
class DraftGenerator:
    """
    从 AI 问答对生成规则草稿。

    触发时机（由外部决定）:
      A. 每次 AI 增强查询后（异步，不阻塞响应）
      B. 定时批量处理（每 30 分钟处理待处理的问答对）

    生成流程:
      [问答对: {query, ai_response, validation_result}]
          ↓
      ① 过滤: 仅处理 CONSISTENT / PARTIAL 的结果
              CONTRADICT / UNVERIFIABLE → 丢弃（质量不可靠）
          ↓
      ② LLM 提炼:
          prompt = f"""
          从以下问答中提炼一条可复用的技术规则。

          要求：
          - title: 20 字以内的概括标题
          - content: 100-300 字的详细说明，包含具体做法和原理
          - category: 从已有分类中选择 [{', '.join(existing_categories)}]
          - tags: 3-5 个关键词标签

          问答对：
          问题：{query}
          回答：{ai_response}
          """
          ↓
      ③ 解析 LLM 返回的 JSON:
          {
              "title": str,
              "content": str,
              "category": str,
              "tags": [str, ...],
          }
          ↓
      ④ 质量检查:
          - title 长度 ≥ 4 且 ≤ 50
          - content 长度 ≥ 50 且 ≤ 2000
          - category 在已有分类列表中（不在 → 归入 general）
          - tags 数量 ≥ 1 且 ≤ 10
          ↓
      ⑤ 返回 draft dict（通过校验）或 None
    """
```

### DualDedup

```python
class DualDedup:
    """
    双路去重：
      A. 标题 TF-IDF 相似度（防止语义重复的规则）
      B. 内容哈希（防止内容完全相同的规则）

    配置:
      title_sim_threshold: 0.7  # 标题余弦相似度 ≥ 0.7 → 重复
      content_hash_method: "sha256"

    方法:
      def check(self, draft: dict) -> DedupResult:
          """检查草稿是否与现有规则重复。"""

      def check_batch(self, drafts: list[dict]) -> list[DedupResult]:
          """批量检查。"""

    DedupResult:
      {
          "is_duplicate": bool,
          "method": "title_sim" | "content_hash" | None,
          "matched_rule_id": str | None,    # 匹配到的现有规则 ID
          "matched_rule_title": str | None,
          "similarity": float,               # 匹配度
      }

    实现:
      A. 标题 TF-IDF:
         复用 gap_detector._compute_tfidf() + _cosine_similarity()
         与所有已有规则标题比较 → max_sim ≥ 0.7 → 重复
         O(n) 扫描，首次加载时全量计算一次并缓存 tfidf 向量

      B. 内容哈希:
         draft 的 content 去空格/换行后 SHA256
         与所有已有规则的 content_hash 比较
         storage_v2 中 rules 表已有 content_hash 计算逻辑
    """
```

### ConfidenceAdjuster

```python
class ConfidenceAdjuster:
    """
    基于用户反馈的置信度调整。

    规则:
      初始 confidence = 0.5（verifier=ai 的新规则）
      每次正面反馈（用户点击/采纳）: confidence += 0.05
      每次负面反馈（用户纠错/忽略）: confidence -= 0.1
      clamp(0.05, 0.95): 永不归零也永不封顶到 1.0

    额外机制:
      - 7 天内无任何反馈 → 触发衰减: confidence *= 0.9
      - confidence ≥ 0.7 且反馈率 > 80% → 自动升级 verifier = "ai_verified"
      - confidence ≤ 0.1 → 自动标记过期

    方法:
      def record_feedback(self, rule_id: str, positive: bool) -> dict:
          """记录一次用户反馈并更新置信度。"""

      def decay_check(self) -> list[str]:
          """对长期无反馈的规则执行衰减，返回被衰减的 rule_id 列表。"""

      def promote_check(self) -> list[str]:
          """检查是否有规则可升级为 ai_verified。"""
    """
```

### AutoIngest（统一入口）

```python
class AutoIngest:
    """
    自动规则提炼统一入口。

    主流程:
      [问答对]
          ↓
      ① DraftGenerator.generate(qa_pair) → draft or None
          ↓ (None → 丢弃）
      ② DualDedup.check(draft) → 重复? → 丢弃
          ↓
      ③ 生成 rule_id: auto_{category}_{timestamp}_{hash8}
          ↓
      ④ 创建 Rule:
          Rule(
              id=rule_id,
              title=draft.title,
              content=draft.content,
              category=draft.category,
              tags=draft.tags,
              confidence=0.5,        # 新规则初始值
              verifier="ai",         # 标记为 AI 生成
              evolution_log=[f"ai_ingested: 来自 AI 问答提炼"],
          )
          ↓
      ⑤ storage_v2.add(rule) → 写入 SQLite + 通知索引
          ↓
      ⑥ log_audit(action="ai_ingest", result="success", ...)
          ↓
      ⑦ stats 计数 + 1

    速率限制:
      - max_per_session: config.ai_bridge.max_new_rules_per_session (默认 10)
      - max_per_day: 50（硬上限，防止质量失控）
      - 达到上限 → 跳过提炼，写日志

    关键方法:
      def process_qa_pair(self, query: str, ai_response: str,
                          validation_result: AIValidationResult) -> Optional[str]:
          """处理单个问答对，返回创建的 rule_id 或 None。"""
          # 仅处理 CONSISTENT / PARTIAL
          if validation_result not in (CONSISTENT, PARTIAL):
              return None
          # 速率检查
          if self._session_count >= self.max_per_session:
              return None
          # 生成草稿 → 去重 → 入库

      def get_stats(self) -> dict:
          """返回提炼统计。"""
    """
```

---

## C3. 存储层变更

### 新表: ai_cache

```sql
CREATE TABLE IF NOT EXISTS ai_cache (
    query_hash TEXT PRIMARY KEY,
    query TEXT NOT NULL,
    response TEXT NOT NULL,
    model TEXT NOT NULL,
    validation TEXT,           -- JSON: AIValidator 结果
    cost_usd REAL NOT NULL DEFAULT 0,
    latency_ms REAL,
    created_at TEXT NOT NULL,
    hit_count INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_ai_cache_created ON ai_cache(created_at);
```

### 新表: ingestion_log

```sql
CREATE TABLE IF NOT EXISTS ingestion_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    query TEXT NOT NULL,
    rule_id TEXT,
    title TEXT,
    category TEXT,
    status TEXT NOT NULL,        -- 'created' | 'duplicate' | 'skipped' | 'failed'
    dedup_method TEXT,           -- 'title_sim' | 'content_hash' | None
    matched_rule_id TEXT,
    error_message TEXT,
    cost_usd REAL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_ingestion_ts ON ingestion_log(timestamp);
CREATE INDEX IF NOT EXISTS idx_ingestion_status ON ingestion_log(status);
```

### 新表: ai_feedback

```sql
CREATE TABLE IF NOT EXISTS ai_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    rule_id TEXT NOT NULL,
    positive INTEGER NOT NULL,    -- 1=正面, 0=负面
    source TEXT DEFAULT 'user',   -- 'user' | 'auto'
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_feedback_rule ON ai_feedback(rule_id);
```

### storage_v2 新增方法

```python
# AI 缓存
def ai_cache_get(self, query_hash: str) -> Optional[dict]:
def ai_cache_set(self, query_hash: str, query: str, response: str,
                 model: str, cost_usd: float, latency_ms: float,
                 validation: Optional[str] = None):
def ai_cache_hit(self, query_hash: str):  # 更新 hit_count
def ai_cache_cleanup(self, max_entries: int = 5000):  # LRU 淘汰

# 提炼日志
def log_ingestion(self, query: str, rule_id: Optional[str],
                  title: Optional[str], category: Optional[str],
                  status: str, dedup_method: Optional[str] = None,
                  matched_rule_id: Optional[str] = None,
                  error_message: Optional[str] = None,
                  cost_usd: float = 0.0):

# AI 反馈
def record_ai_feedback(self, rule_id: str, positive: bool, source: str = "user"):
def get_ai_feedback(self, rule_id: str) -> List[dict]:
```

---

## C4. main.py 集成

### 新 API 路由

```
POST /ai/query          → AI 增强查询
GET  /ai/budget         → 当前预算状态
GET  /ai/stats          → AI 学习统计
POST /ai/feedback       → 提交用户反馈（用于置信度调整）
POST /ingest/run        → 手动触发一次提炼扫描
GET  /ingest/logs       → 提炼日志
```

#### POST /ai/query

```python
@app.post("/ai/query")
async def ai_query(query: str = Query(..., min_length=1)):
    """AI 增强查询。"""
    if not ai_bridge or not ai_bridge.is_enabled():
        return {"error": "AI Bridge 未启用", "source": "system"}
    try:
        result = ai_bridge.enhance_query(query)
        # 异步触发 auto_ingest（不阻塞响应）
        if auto_ingest and result.get("source") in ("ai", "cache"):
            validation = result.get("validation", {})
            try:
                v_result = AIValidationResult(validation.get("result", "unverifiable"))
                auto_ingest.process_qa_pair(query, result["content"], v_result)
            except Exception:
                pass  # 提炼失败不影响查询结果
        return result
    except Exception as e:
        return {"error": str(e), "source": "system"}
```

#### GET /ai/budget

```python
@app.get("/ai/budget")
async def ai_budget_status():
    if not ai_bridge:
        return {"enabled": False}
    return ai_bridge.get_budget_status()
```

#### GET /ai/stats

```python
@app.get("/ai/stats")
async def ai_stats():
    result = {}
    if ai_bridge:
        result["ai_bridge"] = ai_bridge.get_stats()
    if auto_ingest:
        result["auto_ingest"] = auto_ingest.get_stats()
    return result
```

#### POST /ai/feedback

```python
@app.post("/ai/feedback")
async def ai_feedback(rule_id: str = Query(...), positive: bool = Query(...)):
    """提交用户对 AI 生成规则的反馈。"""
    if not storage_v2:
        return {"error": "SQLite 存储未启用"}
    storage_v2.record_ai_feedback(rule_id, positive)
    # 触发置信度调整
    if auto_ingest:
        auto_ingest.confidence_adjuster.record_feedback(rule_id, positive)
    return {"status": "ok"}
```

### v3/status 更新

在 `v3_status()` 响应中增加：

```python
"ai_bridge": {
    "enabled": ai_bridge.is_enabled() if ai_bridge else False,
    "budget": ai_bridge.get_budget_status() if ai_bridge else {},
    "stats": ai_bridge.get_stats() if ai_bridge else {},
},
"auto_ingest": {
    "enabled": auto_ingest is not None,
    "stats": auto_ingest.get_stats() if auto_ingest else {},
},
```

### management_loop 集成

在管理循环的 30s tick 中增加：

```python
# 每 30 秒：检查待处理的 AI 提炼（如有）
if auto_ingest:
    try:
        auto_ingest.process_pending()  # 处理队列中的待提炼项
    except Exception:
        pass

# 每 600 秒：AI 缓存清理
if ai_bridge:
    try:
        storage_v2.ai_cache_cleanup(max_entries=5000)
    except Exception:
        pass

# 每 3600 秒：置信度衰减检查
if auto_ingest:
    try:
        decayed = auto_ingest.confidence_adjuster.decay_check()
        if decayed:
            logger.info("ai", f"置信度衰减: {len(decayed)} 条规则", rules=decayed)
        promoted = auto_ingest.confidence_adjuster.promote_check()
        if promoted:
            logger.info("ai", f"置信度升级: {len(promoted)} 条规则", rules=promoted)
    except Exception:
        pass
```

### 启动初始化

```python
# Phase B 初始化之后...
# Phase C: AI 桥接 + 自动学习
ai_bridge = None
auto_ingest = None
if HAS_V3 and config.get("v3", {}).get("ai_bridge", {}).get("enabled", False):
    try:
        from ai_bridge import AIBridge
        from auto_ingest import AutoIngest
        ai_bridge = AIBridge(storage_v2, config["v3"]["ai_bridge"])
        auto_ingest = AutoIngest(storage_v2, ai_bridge, config["v3"]["ai_bridge"])
        logger.info("v3", "Phase C 模块已初始化 (ai_bridge + auto_ingest)")
    except Exception as e:
        logger.info("v3", f"Phase C 初始化失败: {e}")
```

---

## C5. config.py 配置

```python
"ai_bridge": {
    "enabled": False,                     # 默认关闭，不影响核心查询
    "provider": "claude",                 # claude | openai | local
    "endpoint": "",                       # API 地址
    "api_key_env": "ANTHROPIC_API_KEY",   # 从环境变量读取 API key
    "model": "claude-sonnet-4-6",         # 模型名
    "confidence_threshold": 0.6,          # 系统检索置信度 ≥ 此值 → 不调 AI
    "daily_limit_usd": 5.0,              # 每日预算上限
    "warn_threshold": 0.8,               # 预算告警阈值
    "per_call_limit_usd": 0.5,           # 单次调用上限
    "cache_ttl_hours": 24,               # AI 缓存有效期
    "cache_max_entries": 5000,           # 缓存最大条目数
    "max_new_rules_per_session": 10,     # 每次 session 最多提炼 N 条
    "max_new_rules_per_day": 50,         # 每日最多提炼 N 条
    "temperature": 0.3,                  # LLM 温度
    "max_tokens": 1024,                  # LLM 最大输出 token 数
    "title_dedup_threshold": 0.7,        # 标题去重相似度阈值
},
```

---

## C6. 完整工作流示例

### 场景：用户提问"如何在 Python 中正确处理异步数据库会话？"

```
Step 1: 用户 POST /ai/query?query=如何在 Python 中正确处理异步数据库会话？

Step 2: 系统检索
  → storage.search(query) → 返回规则列表
  → max_confidence = 0.45 < 0.6 (threshold)
  → 进入 AI 增强路径

Step 3: 预算检查
  → AIBudget.allow_call() → True（今日已用 $1.20 / $5.00）
  → 预扣 $0.05（估算成本）

Step 4: 缓存检查
  → SHA256(query) → ai_cache 未命中

Step 5: LLM 调用
  → ClaudeProvider.chat(system_prompt + user_query)
  → 返回回答内容（含代码示例），耗时 2.3s，成本 $0.03

Step 6: AI 验证
  → AIValidator.validate(ai_response)
  → 匹配到规则 python/042（异步数据库最佳实践）
  → 结果: CONSISTENT（与现有规则一致）

Step 7: 扣减预算 + 写入缓存
  → AIBudget.record_cost($0.03)
  → AICache.store(query_hash, response, ...)

Step 8: 返回增强结果（带验证标签）
  → status 200
  → {source: "ai", confidence: 0.7, validation: {result: "consistent", ...}}

Step 9: 异步触发自动提炼
  → AutoIngest.process_qa_pair(query, response, CONSISTENT)
    → DraftGenerator.generate() → {title: "Python 异步数据库会话管理", ...}
    → DualDedup.check() → 与 python/042 标题相似度 0.71 ≥ 0.7 → 重复
    → 丢弃，写 ingestion_log(status="duplicate", matched_rule_id="python/042")
```

### 场景：用户反馈"这条规则对我不适用"

```
Step 1: 用户 POST /ai/feedback?rule_id=auto_python_20260610_a1b2c3d4&positive=false

Step 2: 记录反馈
  → storage_v2.record_ai_feedback("auto_python_...", positive=False)

Step 3: 置信度调整
  → ConfidenceAdjuster.record_feedback("auto_python_...", positive=False)
  → confidence: 0.5 → 0.4

Step 4: 如果多次负面反馈降至 ≤ 0.1
  → 自动标记过期
```

---

## C7. 降级策略矩阵

| 故障场景 | 影响范围 | 表现 |
|---------|---------|------|
| LLM API 不可用 | AI 查询 + 提炼 | 降级为纯系统检索，/ai/query 返回 system 结果 + source=system |
| API key 未配置 | AI 查询 + 提炼 | ai_bridge.is_enabled()=False，所有 AI 路由返回 400 |
| 预算耗尽 | AI 查询 | 返回系统结果 + 标注"AI 预算已耗尽"（仍在响应头带 X-AI-Budget: exhausted） |
| SQLite 不可用 | 缓存 + 日志 | AI 调用正常，但缓存和提炼日志无法持久化（不影响查询） |
| 提炼失败 | 单条提炼 | 写 ingestion_log(status="failed")，不影响后续提炼 |
| 去重检查慢 | 提炼延迟 | O(n) TF-IDF 全量扫描，首次预热后 ~50ms/次（10k 规则） |

---

## 文件清单

| 文件 | 类型 | 预估行数 |
|------|------|---------|
| `ai_bridge.py` | 新建 | ~400 行 |
| `auto_ingest.py` | 新建 | ~350 行 |
| `storage_v2.py` | 修改（+3 表 + 9 方法） | +120 行 |
| `main.py` | 修改（+5 路由 + 初始化 + 管理循环） | +100 行 |
| `config.py` | 修改（+ai_bridge 配置项） | +15 行 |

总计：约 985 行新增/修改代码。
