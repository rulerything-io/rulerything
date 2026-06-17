# CLAUDE.md — Rulerything Skill Integration

Copy this file into your project as `CLAUDE.md`, or merge the relevant sections
into your existing `CLAUDE.md`.

---

## Rule knowledge base (external)

This project has access to **Rulerything** — a self-evolving rule knowledge base
with 1000+ curated rules across 30+ technology categories.

**Use `smart` search to query rules when answering technical questions.**
Server auto-starts on demand.

### Quick commands

```bash
# v2.0 smart search (default) — auto-detects tech stack, semantic + category hybrid:
python /path/to/rulerything-skill/rule_helper.py smart "<your question>"
# Example: python rule_helper.py smart "Python async performance optimization"
#   → auto-detects python + performance categories

# v1.0 fallback:
python /path/to/rulerything-skill/rule_helper.py smart "<question>" --v1

# Standard search:
python /path/to/rulerything-skill/rule_helper.py search "<keyword>" --type prefix

# Browse and inspect:
python /path/to/rulerything-skill/rule_helper.py status
python /path/to/rulerything-skill/rule_helper.py list [--cat CAT]
python /path/to/rulerything-skill/rule_helper.py get <rule_id>

# Server lifecycle:
python /path/to/rulerything-skill/rule_helper.py start
python /path/to/rulerything-skill/rule_helper.py stop
```

### Response rules

- When answering technical questions, proactively use `smart` search
  to find relevant rules
- If a rule matches, display a trigger block at the top of your response:

  ```
  触发规则 (2条):
    • security/001 · SQL注入防护 · 置信度 0.95
    • python/042 · 异步上下文管理器 · 置信度 0.82
  ```

- Use the rule as supporting context, not the sole answer
- If rules don't apply, omit the trigger block and answer normally

### How to set up auto-start

Add the following to your Claude Code settings (settings.json):

```json
{
  "hooks": {
    "PostSessionStart": "bash /path/to/rulerything-skill/hooks/post-session-start.sh"
  }
}
```

This starts the rule server in the background when a Claude Code session begins.
