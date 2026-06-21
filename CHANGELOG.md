# Changelog

## 1.4.0

- Build a complete wheel containing runtime modules, static assets and 994 rules.
- Use one version source and expose `rulerything` / `rulerything-server` commands.
- Make module import side-effect free; initialize and close resources in app lifespan.
- Define strict `exact`, `prefix` and `tag` search contracts; make hybrid retrieval explicit as `smart`.
- Count searches and cache hits per query rather than per matched rule.
- Select one writable repository; use JSONL only to seed an empty SQLite database.
- Decode legacy JSON-string `evolution_log` fields safely.
- Disable optional autonomous subsystems by default.
