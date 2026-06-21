"""Stable storage boundary tests."""

import tempfile
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.repository import create_repository
from rule import Rule
from storage import RuleStorage
from storage_v2 import RuleStorageV2
from index import EverythingStyleIndex
from evolution import EvolutionEngine, EvolutionType


def test_sqlite_is_seeded_once_from_jsonl():
    root = Path(tempfile.mkdtemp())
    seed_dir = root / "seed"
    runtime_dir = root / "runtime"
    source = RuleStorage(str(seed_dir))
    source.add(Rule(id="seed/001", title="Seed", content="content"))

    repository, sqlite = create_repository(
        {"storage": {"backend": "sqlite"}}, str(runtime_dir), str(seed_dir)
    )
    assert isinstance(repository, RuleStorageV2)
    assert sqlite is repository
    assert [rule.id for rule in repository.list()] == ["seed/001"]

    source.add(Rule(id="seed/002", title="Second", content="other"))
    repository2, _ = create_repository(
        {"storage": {"backend": "sqlite"}}, str(runtime_dir), str(seed_dir)
    )
    assert [rule.id for rule in repository2.list()] == ["seed/001"]


def test_jsonl_backend_is_the_only_repository():
    runtime_dir = tempfile.mkdtemp()
    repository, sqlite = create_repository(
        {"storage": {"backend": "jsonl"}}, runtime_dir
    )
    assert isinstance(repository, RuleStorage)
    assert sqlite is None


def test_inactive_rows_do_not_trigger_reseeding():
    root = Path(tempfile.mkdtemp())
    seed_dir = root / "seed"
    runtime_dir = root / "runtime"
    seed = RuleStorage(str(seed_dir))
    seed.add(Rule(id="seed/001", title="Seed", content="content"))
    repository = RuleStorageV2(str(runtime_dir))
    repository.add(Rule(id="existing/001", title="Old", content="old"))
    repository.delete("existing/001")

    reopened, _ = create_repository(
        {"storage": {"backend": "sqlite"}}, str(runtime_dir), str(seed_dir)
    )
    assert reopened.count_all() == 1
    assert reopened.get("seed/001") is None


def test_seed_preserves_inactive_jsonl_rows():
    root = Path(tempfile.mkdtemp())
    seed = RuleStorage(str(root / "seed"))
    seed.add(Rule(id="inactive/001", title="Inactive", content="content"))
    seed.delete("inactive/001")
    repository, _ = create_repository(
        {"storage": {"backend": "sqlite"}}, str(root / "runtime"), str(root / "seed")
    )
    assert repository.count_all() == 1
    assert repository.list() == []


def test_search_hits_are_persisted_in_one_repository():
    root = tempfile.mkdtemp()
    repository = RuleStorageV2(root)
    repository.add(Rule(id="hit/001", title="Hit", content="content"))
    index = EverythingStyleIndex(repository.list())
    index.set_hit_callback(repository.record_hits)
    index.search("Hit", "exact")
    assert repository.get("hit/001").hit_count == 1


def test_evolution_persists_through_sqlite_repository():
    root = tempfile.mkdtemp()
    repository = RuleStorageV2(root)
    repository.add(Rule(id="evolve/001", title="Evolve", content="content"))
    index = EverythingStyleIndex(repository.list())
    repository.set_index_callback(
        lambda action, data: index.add(repository.get(data)) if action == "update" else None
    )
    engine = EvolutionEngine(repository, index, data_dir=root)
    engine._pending.append(
        (EvolutionType.CONFIDENCE_ADJUST, "evolve/001", {"delta": -0.1})
    )
    changes = engine.apply_pending_evolutions(dry_run=False)
    assert len(changes) == 1
    assert repository.get("evolve/001").confidence == 0.4
    assert repository.get("evolve/001").version == 2
