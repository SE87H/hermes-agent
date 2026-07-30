from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SNAPSHOT_PATH = ROOT / "scripts" / "ava_runtime" / "state_snapshot.py"


def _load_module():
    name = "ava_runtime_state_snapshot_test"
    spec = importlib.util.spec_from_file_location(name, SNAPSHOT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _home(tmp_path: Path, entity: str = "ava") -> Path:
    home = tmp_path / "state" / entity
    home.mkdir(parents=True)
    with sqlite3.connect(home / "state.db") as conn:
        conn.execute("CREATE TABLE sessions (id TEXT PRIMARY KEY, payload TEXT)")
        conn.execute("INSERT INTO sessions VALUES (?, ?)", ("s1", "private transcript marker"))
        conn.execute("PRAGMA user_version=7")
        conn.commit()
    return home


def test_create_and_verify_atomic_snapshot(tmp_path):
    module = _load_module()
    home = _home(tmp_path, "ava")
    output = tmp_path / "backups"
    snapshot = module.create_snapshot(entity="ava", hermes_home=home, output_root=output)
    result = module.verify_snapshot(snapshot)
    assert snapshot.parent == output.resolve()
    assert result["entity"] == "ava"
    assert len(result["state_db_sha256"]) == 64
    assert snapshot.stat().st_mode & 0o777 == 0o700
    assert (snapshot / "state.db").stat().st_mode & 0o777 == 0o600
    assert (snapshot / "manifest.json").stat().st_mode & 0o777 == 0o600
    manifest = json.loads((snapshot / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["sqlite"]["integrity_check"] == "ok"
    assert manifest["sqlite"]["user_version"] == 7


def test_online_backup_is_independent_from_later_source_changes(tmp_path):
    module = _load_module()
    home = _home(tmp_path, "aeon")
    snapshot = module.create_snapshot(entity="aeon", hermes_home=home, output_root=tmp_path / "backups")
    with sqlite3.connect(home / "state.db") as conn:
        conn.execute("INSERT INTO sessions VALUES (?, ?)", ("s2", "later"))
        conn.commit()
    with sqlite3.connect(snapshot / "state.db") as conn:
        count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    assert count == 1
    module.verify_snapshot(snapshot)


def test_tampered_snapshot_is_rejected(tmp_path):
    module = _load_module()
    home = _home(tmp_path, "avaeon-codex")
    snapshot = module.create_snapshot(entity="avaeon-codex", hermes_home=home, output_root=tmp_path / "backups")
    with (snapshot / "state.db").open("ab") as handle:
        handle.write(b"tamper")
    with pytest.raises(module.SnapshotError, match="checksum mismatch"):
        module.verify_snapshot(snapshot)


def test_output_inside_hermes_home_is_rejected(tmp_path):
    module = _load_module()
    home = _home(tmp_path, "ava")
    with pytest.raises(module.SnapshotError, match="outside HERMES_HOME"):
        module.create_snapshot(entity="ava", hermes_home=home, output_root=home / "backup")


def test_wrong_entity_scope_is_rejected(tmp_path):
    module = _load_module()
    home = _home(tmp_path, "ava")
    with pytest.raises(module.SnapshotError, match="not visibly scoped"):
        module.create_snapshot(entity="aeon", hermes_home=home, output_root=tmp_path / "backup")


def test_symlinked_source_db_is_rejected(tmp_path):
    module = _load_module()
    real = tmp_path / "real.db"
    with sqlite3.connect(real) as conn:
        conn.execute("CREATE TABLE x (id INTEGER)")
    home = tmp_path / "state" / "ava"
    home.mkdir(parents=True)
    (home / "state.db").symlink_to(real)
    with pytest.raises(module.SnapshotError, match="symlinked"):
        module.create_snapshot(entity="ava", hermes_home=home, output_root=tmp_path / "backup")


def test_missing_state_db_fails_visibly(tmp_path):
    module = _load_module()
    home = tmp_path / "state" / "aeon"
    home.mkdir(parents=True)
    with pytest.raises(module.SnapshotError, match="does not exist"):
        module.create_snapshot(entity="aeon", hermes_home=home, output_root=tmp_path / "backup")
