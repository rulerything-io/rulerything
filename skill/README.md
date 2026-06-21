# Rulerything — AI Coding Agent Skill Package

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

**Rulerything** is a self-evolving rule knowledge base engine. This skill package integrates it with AI coding agents — [Claude Code](https://claude.ai/code), [Codex CLI](https://github.com/openai/codex), and others — giving them deterministic access to 1000+ curated programming rules across 30+ technology categories.

When your AI agent answers a technical question, it automatically queries the rule base for relevant best practices, design patterns, security guidelines, and anti-patterns — then cites them as supporting context.

## How It Works

```
User asks technical question
        │
        ▼
Claude Code / Codex CLI ──smart search──▶ Rulerything Server (FastAPI)
        │                                       │
        │                                       ▼
        │                                 Matched rules
        │                                       │
        ▼                                       ▼
AI agent responds with:
  • Answer based on general knowledge
  • Triggered rules displayed as citations
  • Confidence scores for each rule
```

## Prerequisites

- **Claude Code** (CLI or IDE extension) **or** **Codex CLI** (or other AI coding agent)
- **Python 3.10+**
- **Rulerything server** — already included if you cloned this repo

## Quick Install

```bash
# 1. Clone the main repo (includes both server and skill)
git clone https://github.com/rulerything-io/rulerything.git
cd rulerything

# 2. Install server dependencies
pip install fastapi uvicorn pyyaml numpy scipy
pip install scikit-learn jieba rank-bm25  # optional but recommended

# 3. Install the skill
## For Claude Code:
python skill/install.py --project /path/to/your/project

## For Codex CLI:
# Copy the skill to Codex skills directory:
cp -r skill/codex_query.py ~/.codex/skills/rulerything/scripts/
cp skill/CODEX.md ~/.codex/skills/rulerything/SKILL.md
```

## Usage

### Claude Code

Add the following to your project's `CLAUDE.md` (the installer does this automatically):

```markdown
## Rule knowledge base

This project has access to **Rulerything** — a self-evolving rule knowledge base
with 1000+ rules across 30+ technology categories.

When answering technical questions, query the rule base:
python /path/to/rulerything/skill/rule_helper.py smart "<your question>"

**Response rule:** If rules match, display a trigger block at the top:
触发规则 (2条):
  • security/001 · SQL注入防护 · 置信度 0.95
  • python/042 · 异步上下文管理器 · 置信度 0.82
```

#### Auto-start hooks

The installer can set up Claude Code hooks that automatically start the rule
server when a session begins:

```bash
python skill/install.py --project /path/to/your/project --setup-hook
```

### Codex CLI

Copy `CODEX.md` into your Codex skills directory as `SKILL.md` and
`codex_query.py` into the `scripts/` subdirectory:

```bash
# Typical installation paths:
cp skill/CODEX.md ~/.codex/skills/rulerything/SKILL.md
cp skill/codex_query.py ~/.codex/skills/rulerything/scripts/
```

The `CODEX.md` file contains the skill metadata and usage instructions. Codex
will automatically invoke `codex_query.py` when relevant queries arise.

### Manual CLI usage (both Claude Code and Codex)

```bash
# Smart search (auto-detects tech stack)
python rule_helper.py smart "Python async performance optimization"

# v1.0 fallback
python rule_helper.py smart "Python async" --v1

# Standard search
python rule_helper.py search "SQL injection" --type prefix
python rule_helper.py search "rate limiting" --type prefix --cat security

# Browse rules
python rule_helper.py list
python rule_helper.py list --cat security

# View rule details
python rule_helper.py get security/001

# Server management
python rule_helper.py start
python rule_helper.py stop
python rule_helper.py restart
python rule_helper.py status

# AI delegation
python rule_helper.py ai-pending
python rule_helper.py ai-respond q_20260610_220701_94d2e2 --file response.txt
```

## Configuration

The skill is configured via environment variables or `config.yaml`:

| Variable | Default | Description |
|---|---|---|
| `RULERYTHING_DIR` | `../rulerything` | Path to the rulerything rule system |
| `RULERYTHING_PORT` | `8001` | API port for the rule server |
| `RULERYTHING_AUTO_START` | `true` | Auto-start server on query if not running |
| `RULERYTHING_LOG` | `$RULERYTHING_DIR/logs/rule_triggers.log` | Rule trigger log path |

## Project Structure

```
rulerything/                   # Main repo root
├── main.py                    # Rulerything server (FastAPI)
├── cli.py                     # Server CLI
├── ...                        # Server source files (30+ modules)
│
├── skill/                     # ← This package
│   ├── README.md              # This file
│   ├── CLAUDE.md              # Claude Code integration reference
│   ├── CODEX.md               # Codex CLI integration reference
│   ├── rule_helper.py         # CLI helper — Claude Code integration point
│   ├── codex_query.py         # Direct query script — Codex integration point
│   ├── config.yaml.example    # Example configuration
│   ├── install.py             # One-command installer (Claude Code)
│   ├── hooks/
│   │   ├── post-session-start.sh   # Auto-start hook (Linux/macOS)
│   │   ├── post-session-start.ps1  # Auto-start hook (Windows)
│   │   └── README.md               # Hook setup guide
│   └── .gitignore
│
├── data/                      # Rule data files (JSONL)
├── core/                      # Server core modules
├── routes/                    # API routes
├── tests/                     # Test suite
├── config.yaml                # Server configuration
├── pyproject.toml
└── LICENSE
```

## Architecture

The skill delegates to the **Rulerything server** (a separate FastAPI application):

```
┌───────────────────────┐   HTTP/JSON    ┌──────────────────────┐
│  Claude Code           │ ──────────────▶│  Rulerything Server  │
│  (rule_helper.py)      │◀────────────── │  (FastAPI, port 8001)│
│                        │  matched rules  │                      │
│  Codex CLI             │                │  ┌────────────────┐  │
│  (codex_query.py)      │                │  │ Entropy Engine │  │
│                        │                │  │ (Phase 1)      │  │
│  Other AI Agents       │                │  ├────────────────┤  │
│  (HTTP API direct)     │                │  │ Immune System  │  │
│                        │                │  │ (Phase 2)      │  │
│                        │                │  ├────────────────┤  │
│                        │                │  │ Adaptive Sys.  │  │
│                        │                │  │ (Phase 3)      │  │
│                        │                │  └────────────────┘  │
└───────────────────────┘                └──────────────────────┘
```

- **Phase 1 Entropy Engine** — Performance monitoring & self-tuning
- **Phase 2 Immune System** — Quality scoring, conflict detection, auto-cleanup
- **Phase 3 Adaptive System** — Self-optimization without human intervention

## How Rules Evolve

Rules are not static. The system:
1. **Tracks** every query and its results
2. **Detects** gaps — queries that returned no relevant rules
3. **Proposes** new rules via AI Bridge (LLM-powered)
4. **Validates** proposed rules against existing ones (conflict detection)
5. **Evolves** — rules gain/lose confidence based on real usage

## License

Apache 2.0 — see [LICENSE](LICENSE).
