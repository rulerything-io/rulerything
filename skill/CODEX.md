---
name: rulerything
description: Use when Codex should consult the local Rulerything rule knowledge base before giving programming, architecture, security, performance, testing, refactoring, code review, or implementation guidance. Trigger for engineering decisions where verified best-practice rules, project conventions, or deterministic rule retrieval can improve the answer.
---

# Rulerything

Rulerything is a local rule knowledge base for engineering best practices. Use it as decision support before making technical recommendations or code changes.

## Query Workflow

1. Query Rulerything when the task involves code quality, architecture, security, performance, testing, API design, refactoring, framework choices, or review findings.
2. Use a concise engineering query that describes the real decision, not only the user's exact wording.
3. Prefer `--type exact` first; if results are weak or empty, retry with `--type tag` or `--type prefix`.
4. Apply returned rules critically. Cite rule ids when they materially influence the answer.
5. If no rule applies, continue with normal engineering judgment and say the rule base had no useful match only when relevant.

## Commands

Run the bundled query helper:

```powershell
python /path/to/rulerything/skill/codex_query.py "Python async performance"
```

Useful options:

```powershell
python /path/to/rulerything/skill/codex_query.py "SQL injection prevention" --type tag --limit 5
python /path/to/rulerything/skill/codex_query.py "API error handling" --category api --format json
```

The helper locates the Rulerything project through `RULERYTHING_HOME`; if unset, it uses the parent directory of the `skill/` folder.

## Output Use

For each relevant rule, preserve:

- rule id, for attribution
- title and category, for fit
- confidence, as a weak signal rather than absolute truth
- content, as the actionable guidance

Do not paste long rule content unless the user asks. Summarize the rule and connect it to the current task.
