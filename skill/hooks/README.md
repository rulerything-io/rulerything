# Hooks — Auto-Start the Rule Server

These hooks automatically start the Rulerything server when a Claude Code
session begins, eliminating cold-start delay on the first query.

## Installation

### Claude Code (settings.json)

Add the following to your `settings.json` (typically at
`~/.claude/settings.json` or project-level `.claude/settings.json`):

```json
{
  "hooks": {
    "PostSessionStart": "bash /path/to/rulerything-skill/hooks/post-session-start.sh"
  }
}
```

### Claude Code (CLI flag)

Alternatively, pass the hook when starting Claude Code:

```bash
claude --hook PostSessionStart="bash /path/to/rulerything-skill/hooks/post-session-start.sh"
```

### Environment variables

```bash
# Custom rulerything directory
export RULERYTHING_DIR=/custom/path/rulerything

# Custom port (must match rule_helper.py config)
export RULERYTHING_PORT=8002
```

## Files

- **`post-session-start.sh`** — Bash script that checks if the server is
  already running, and starts it if not. Compatible with Linux, macOS, and
  Windows (Git Bash / WSL).
