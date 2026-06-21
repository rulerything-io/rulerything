"""Storage boundary for the stable core.

Exactly one repository is selected for a process.  JSONL may seed an empty
SQLite database, but it is never kept as a second writable store.
"""

from pathlib import Path
from typing import Optional, Tuple

from storage import RuleStorage
from storage_v2 import RuleStorageV2


def create_repository(config: dict, data_dir: str, seed_dir: Optional[str] = None) \
        -> Tuple[object, Optional[RuleStorageV2]]:
    backend = config.get("storage", {}).get(
        "backend", config.get("v3", {}).get("storage", "sqlite")
    )
    if backend not in {"sqlite", "jsonl"}:
        raise ValueError(f"unsupported storage backend: {backend}")

    if backend == "jsonl":
        return RuleStorage(data_dir), None

    repository = RuleStorageV2(data_dir)
    if repository.count_all() == 0:
        source = Path(seed_dir or data_dir)
        if source.is_dir() and any(source.glob("*.jsonl")):
            seed = RuleStorage(str(source))
            for rule in seed.list_all():
                ok, message = repository.add(rule)
                if not ok:
                    raise RuntimeError(f"failed to seed {rule.id}: {message}")
    return repository, repository
