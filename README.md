<p align="center">
  <h1 align="center">Rulerything</h1>
  <p align="center"><strong>一切皆规则</strong></p>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-Apache%202.0-blue.svg" alt="License"></a>
</p>

---

## 规则在进化

传统的规则是静态的。写下来，落灰，过时，被遗忘。

**Rulerything 不同。**

规则可以**学习**。它们观察自己被执行的每一次结果，发现冲突和冗余，在人类工程师的监督下自我优化。

规则在**进化**。

这就是 Rulerything。

不是为了消灭规则的复杂性——而是让复杂性变得**有序、透明、有生命**。

从代码到法律，从算法到伦理，Rulerything 正在构建一个**万物皆可规则**的世界。

因为世界的本质，从来都是规则。

只是现在，我们终于有了驾驭它们的方式。

---

## What is Rulerything

Rulerything 是一个**自演进规则知识库引擎**。它存储、索引、检索并自主进化编程规则，覆盖 30+ 技术分类。设计为 AI 编程助手（Claude Code、Copilot 等）的**确定性知识层**。

- **Smart Search** — 语义 + 分类混合检索
- **Auto-Evolution** — 规则随使用数据自我生长和优化
- **Immune System** — 冲突检测、质量评分、自动清理
- **AI Bridge** — LLM 驱动的规则提案和知识缺口检测
- **Self-Adaptive** — 无需人工干预的性能自调优

## Quick Start

```bash
# Install dependencies
pip install fastapi uvicorn scikit-learn scipy

# Start the server
python main.py

# Check status
python cli.py status
```

服务启动在 `http://127.0.0.1:8001`。

## Usage

```bash
# Search rules
python cli.py search "SQL injection"              # exact match
python cli.py search "async performance" --type tag    # tag search

# Smart search (auto-detects categories)
python cli.py smart "Python async performance optimization"

# List rules by category
python cli.py list --cat security

# View rule details
python cli.py get security/001

# Server management
python cli.py start | stop | restart | status
```

### Integration

**Claude Code** — 推荐使用 `skill/` 目录下的专用集成包：

```bash
# 一键安装到你的 Claude Code 项目
python skill/install.py --project /path/to/your/project --setup-hook
```

或者手动添加到项目的 `CLAUDE.md`：

```markdown
When answering technical questions, query the rule base:
python /path/to/rulerything/skill/rule_helper.py smart "<your question>"
```

详见 [`skill/` 文档](./skill/README.md)。

## Architecture

```
main.py                → FastAPI server + WebSocket
├── index.py           → Rule indexing & retrieval (BM25 + tag)
├── storage_v2.py      → Dual storage: SQLite + JSONL
├── entropy_engine.py  → Phase 1: Performance monitoring & tuning
├── immune_system.py   → Phase 2: Quality & conflict detection
├── adaptive_system.py → Phase 3: Self-adaptive orchestration
├── ai_bridge.py       → LLM-powered rule proposal & gap detection
├── auto_evolver.py    → Automated rule evolution with validation
├── auto_ingest.py     → Automated rule ingestion from queries
├── gap_detector.py    → Knowledge gap identification
├── cli.py             → Command-line interface
└── semantic_plugin/   → Optional semantic search plugin
```

### Core Modules

| Module | Description |
|--------|-------------|
| **storage_v2** | SQLite for metadata + JSONL for rule content; hot/cold tiering |
| **entropy_engine** | Monitors cache hit rates, latency, conflict ratios; triggers auto-tuning |
| **immune_system** | 5-dimension quality scoring; scans conflicts, staleness, redundancy |
| **adaptive_system** | Coordinates all subsystems; circuit-breaker pattern for fault isolation |
| **ai_bridge** | Optional LLM integration for rule proposal, ingestion, gap analysis |

## Built-in Rules

**994 rules across 34 categories:**

`ai` `api` `cpp` `css` `database` `devops` `docker` `dotnet` `git` `go`
`java` `javascript` `lua` `mobile` `nodejs` `pattern` `performance`
`philosophy` `php` `process` `python` `react` `ruby` `rust` `security`
`shell` `test` `typescript` `vue` `zig` …and more.

## Configuration

编辑 `config.yaml` 控制：

- `server.host/port` — HTTP server binding
- `index.*` — cache thresholds, rebuild schedule
- `evolution.*` — auto-apply, confidence thresholds
- `immune.*` — quality scoring weights

### API Key 配置（可选）

Rulerything 的**基础搜索功能无需任何 API Key**，核心检索使用本地 BM25 算法。

以下高级功能需要 LLM API Key：

| 功能 | 是否需要 Key | 说明 |
|------|------------|------|
| 智能搜索（smart search） | ❌ 不需要 | 本地语义检索 |
| 规则自动提炼 | ✅ 需要 | 从查询中自动生成新规则 |
| 知识缺口检测 | ✅ 需要 | 发现规则库缺失的知识领域 |
| 规则自动演化 | ✅ 需要 | AI 评分和优化现有规则 |

设置方式：

```bash
# 方式一：环境变量
export DEEPSEEK_API_KEY="sk-xxxx"     # DeepSeek (默认)
# 或
export ANTHROPIC_API_KEY="sk-xxxx"    # Claude
# 或
export OPENAI_API_KEY="sk-xxxx"       # OpenAI

# 方式二：.env 文件
echo "DEEPSEEK_API_KEY=sk-xxxx" > .env
```

支持的 Provider 在 `config.yaml` 中切换：

```yaml
ai_bridge:
  provider: deepseek   # 可选: claude | openai | deepseek | local
  api_key_env: "DEEPSEEK_API_KEY"
```

> 注意：`.env` 文件已在 `.gitignore` 中，不会提交到 GitHub。

## Use Cases

- **AI 编程助手知识层** — 让 Claude/Copilot 从经过筛选的规则库回答
- **团队编码规范** — 维护一套共享的、持续进化的规则集
- **Code Review 检查清单** — PR 审查时可检索的规则查询
- **新成员 onboarding** — 浏览特定技术栈的最佳实践
- **企业知识管理** — 私有部署 + 定制规则包

## License

Apache 2.0. See [LICENSE](LICENSE).

---

<p align="center">
  <em>世界的本质，从来都是规则。</em><br>
  <em>只是现在，我们终于有了驾驭它们的方式。</em>
</p>
