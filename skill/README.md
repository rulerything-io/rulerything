# Rulerything — Claude Code Skill

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-blue.svg)](LICENSE)

**Rulerything** is a self-evolving rule knowledge base engine. This skill package integrates it with [Claude Code](https://claude.ai/code), giving Claude deterministic access to 1000+ curated programming rules across 30+ technology categories.

When Claude answers a technical question, it automatically queries the rule base for relevant best practices, design patterns, security guidelines, and anti-patterns — then cites them as supporting context.

## How It Works

```
User asks technical question
        │
        ▼
Claude Code ──smart search──▶ Rulerything Server (FastAPI)
        │                           │
        │                           ▼
        │                     Matched rules
        │                           │
        ▼                           ▼
Claude responds with:
  • Answer based on general knowledge
  • Triggered rules displayed as citations
  • Confidence scores for each rule
```

## Prerequisites

- **Claude Code** (CLI or IDE extension)
- **Python 3.10+**
- **Rulerything server** — cloned and running (see below)

## Quick Install

```bash
# 1. Clone this skill repo
git clone https://github.com/rulerything-io/rulerything-skill.git
cd rulerything-skill

# 2. Clone the rulerything server (if not already done)
git clone https://github.com/rulerything-io/rulerything.git ../rulerything

# 3. Install server dependencies
cd ../rulerything
pip install fastapi uvicorn pyyaml numpy scipy
pip install scikit-learn jieba rank-bm25  # optional but recommended
cd ../rulerything-skill

# 4. Install the skill into your Claude Code project
python install.py --project /path/to/your/project
```

## Usage

### Query rules from your project

Once installed, add the following to your project's `CLAUDE.md` (the installer does this automatically):

```markdown
## Rule knowledge base

This project has access to **Rulerything** — a self-evolving rule knowledge base
with 1000+ rules across 30+ technology categories.

When answering technical questions, query the rule base:
python /path/to/rulerything-skill/rule_helper.py smart "<your question>"

**Response rule:** If rules match, display a trigger block at the top:
触发规则 (2条):
  • security/001 · SQL注入防护 · 置信度 0.95
  • python/042 · 异步上下文管理器 · 置信度 0.82
```

### Manual CLI usage

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

### Auto-start hooks

For a seamless experience, the installer can set up Claude Code hooks that
automatically start the rule server when a session begins.

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
rulerything-skill/
├── README.md                 # This file
├── CLAUDE.md                 # Claude Code integration reference
├── rule_helper.py            # CLI helper — the main integration point
├── config.yaml.example       # Example configuration
├── install.py                # One-command installer
├── hooks/
│   ├── post-session-start.sh # Auto-start hook template
│   └── README.md             # Hook setup guide
├── .gitignore
└── LICENSE
```

## Architecture

The skill delegates to the **Rulerything server** (a separate FastAPI application):

```
┌─────────────────────┐     HTTP/JSON      ┌──────────────────────┐
│  Claude Code         │ ──────────────────▶│  Rulerything Server  │
│  (rule_helper.py)    │◀────────────────── │  (FastAPI, port 8001)│
│                      │  matched rules      │                      │
│                      │                     │  ┌────────────────┐  │
│                      │                     │  │ Entropy Engine │  │
│                      │                     │  │ (Phase 1)      │  │
│                      │                     │  ├────────────────┤  │
│                      │                     │  │ Immune System  │  │
│                      │                     │  │ (Phase 2)      │  │
│                      │                     │  ├────────────────┤  │
│                      │                     │  │ Adaptive Sys.  │  │
│                      │                     │  │ (Phase 3)      │  │
│                      │                     │  └────────────────┘  │
└─────────────────────┘                     └──────────────────────┘
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
