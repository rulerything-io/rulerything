# Rule-KB

**自适应规则知识库系统** — 一个智能的、自演进的编程规则管理引擎。

[![License](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

---

## Overview

Rule-KB stores, indexes, retrieves, and autonomously evolves a knowledge base of programming rules across 30+ technology categories. Designed as a **drop-in knowledge layer for AI coding assistants** (Claude Code, Copilot, etc.), it provides:

- **Smart search** — semantic + category hybrid retrieval
- **Auto-evolution** — rules grow and improve with usage data
- **Immune system** — conflict detection, quality scoring, auto-cleanup
- **AI bridge** — LLM-powered rule proposal and knowledge gap detection

## Quick Start

```bash
# Install dependencies
pip install fastapi uvicorn scikit-learn scipy

# Start the server
python main.py

# Check status
python cli.py status
```

Server starts at `http://127.0.0.1:8001`.

## Usage

```bash
# Search rules
python cli.py search "SQL injection"          # exact match
python cli.py search "async performance" --type tag  # tag search

# Smart search (auto-detects categories)
python cli.py smart "Python async performance optimization"

# List rules by category
python cli.py list --cat security

# View rule details
python cli.py get security/001

# Server management
python cli.py start | stop | restart | status
```

### Integration with AI Coding Assistants

**Claude Code** — Add to `CLAUDE.md`:
```markdown
When answering technical questions, query the rule base:
python /path/to/rule-kb/cli.py smart "<your question>"
```

## Architecture

```
main.py          → FastAPI server + WebSocket
├── index.py     → Rule indexing & retrieval (BM25 + tag hybrid)
├── storage_v2.py → Dual storage: SQLite + JSONL
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

### Core modules

| Module | Description |
|--------|-------------|
| **storage_v2** | SQLite for metadata + JSONL for rule content; hot/cold tiering |
| **entropy_engine** | Monitors cache hit rates, latency, conflict ratios; triggers auto-tuning |
| **immune_system** | 5-dimension quality scoring; scans for conflicts, staleness, redundancy |
| **adaptive_system** | Coordinates all subsystems; circuit-breaker pattern for fault isolation |
| **ai_bridge** | Optional LLM integration for rule proposal, ingestion, gap analysis |

## Data

Built-in rules: **994 rules across 34 categories** including:

`ai` `api` `cpp` `css` `database` `devops` `docker` `dotnet` `git` `go`
`java` `javascript` `lua` `mobile` `nodejs` `pattern` `performance`
`philosophy` `php` `process` `python` `react` `ruby` `rust` `security`
`shell` `test` `typescript` `vue` `zig` …and more.

## Configuration

Edit `config.yaml` to control:

- `server.host/port` — HTTP server binding
- `index.*` — cache thresholds, rebuild schedule
- `evolution.*` — auto-apply, confidence thresholds
- `immune.*` — quality scoring weights
- `ai_bridge.*` — LLM provider, budget limits (requires API key)

## Use Cases

- **AI coding assistant knowledge layer** — lets Claude/Copilot answer from a curated rule base
- **Team coding standards** — maintain a shared, evolving rule set
- **Code review checklists** — searchable rule queries during PR review
- **Training onboarding** — browse technology-specific best practices

## License

Apache 2.0. See [LICENSE](LICENSE).
