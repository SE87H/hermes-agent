from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
import yaml

from hermes_cli.ava_runtime.fleet_config import FleetConfigError, load_fleet_config


def _raw(tmp_path: Path) -> dict:
    return {
        "version": 1,
        "snapshots": {"root": str(tmp_path / "backups")},
        "promotion": {
            "upstream_snapshot": "ava/upstream-2026-07-30",
            "staging": "ava/staging",
            "stable": "ava/stable",
            "require_shadow_validation": True,
            "require_rollback_ref": True,
        },
        "policy": {
            "explicit_hermes_home": "required",
            "resume_missing_session": "fail",
            "resume_missing_workspace": "fail",
            "resume_ambiguous_lineage": "fail",
            "restore_recorded_workspace": True,
            "allow_restore_workspace_opt_out": True,
            "global_most_recent_session": "forbidden_for_managed_entities",
            "terminal_backend_failure": "fail",
            "deployment_ref": "pinned_commit_or_stable_tag",
            "secrets_in_repository": "forbidden",
        },
        "source": {
            "repository": str(tmp_path / "source"),
            "expected_ref": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "require_clean_checkout": True,
            "auto_update": False,
        },
        "entities": {
            "ava": {"hermes_home": str(tmp_path / "state" / "ava"), "workspace": str(tmp_path / "workspaces" / "ava"), "profile": "ava", "session_scope": "ava"},
            "aeon": {"hermes_home": str(tmp_path / "state" / "aeon"), "workspace": str(tmp_path / "workspaces" / "aeon"), "profile": "aeon", "session_scope": "aeon"},
            "avaeon-codex": {"hermes_home": str(tmp_path / "state" / "avaeon-codex"), "workspace": str(tmp_path / "workspaces" / "avaeon-codex"), "profile": "avaeon-codex", "session_scope": "avaeon-codex"},
        },
    }


def _write(tmp_path: Path, raw: dict) -> Path:
    path = tmp_path / "fleet.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def test_valid_fleet_loads_and_renders_environment(tmp_path):
    path = _write(tmp_path, _raw(tmp_path))
    config = load_fleet_config(path)
    assert set(config.entities) == {"ava", "aeon", "avaeon-codex"}
    assert config.entity("avaeon_codex").name == "avaeon-codex"
    env = config.environment("ava")
    assert env["AVA_ENTITY"] == "ava"
    assert env["HERMES_HOME"].endswith("/state/ava")
    assert env["AVA_HERMES_EXPECTED_REF"] == "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
    assert config.raw_sha256 == hashlib.sha256(path.read_bytes()).hexdigest()
    assert "export AVA_ENTITY=aeon" in config.render_shell_environment("aeon")
    assert "OPENAI_API_KEY" not in config.render_shell_environment("aeon")


def test_requires_complete_three_entity_fleet(tmp_path):
    raw = _raw(tmp_path)
    del raw["entities"]["aeon"]
    with pytest.raises(FleetConfigError, match="complete managed fleet"):
        load_fleet_config(_write(tmp_path, raw))


def test_rejects_shared_hermes_home(tmp_path):
    raw = _raw(tmp_path)
    shared = str(tmp_path / "state" / "ava" / "aeon")
    raw["entities"]["ava"]["hermes_home"] = shared
    raw["entities"]["aeon"]["hermes_home"] = shared
    with pytest.raises(FleetConfigError, match="HERMES_HOME collision"):
        load_fleet_config(_write(tmp_path, raw))


def test_rejects_nested_hermes_homes(tmp_path):
    raw = _raw(tmp_path)
    raw["entities"]["aeon"]["hermes_home"] = str(tmp_path / "state" / "ava" / "aeon")
    with pytest.raises(FleetConfigError, match="nested HERMES_HOME"):
        load_fleet_config(_write(tmp_path, raw))


def test_rejects_workspace_inside_any_entity_home(tmp_path):
    raw = _raw(tmp_path)
    raw["entities"]["aeon"]["workspace"] = str(tmp_path / "state" / "ava" / "aeon-workspace")
    with pytest.raises(FleetConfigError, match="workspace .* is inside"):
        load_fleet_config(_write(tmp_path, raw))


def test_rejects_duplicate_session_scope(tmp_path):
    raw = _raw(tmp_path)
    raw["entities"]["aeon"]["session_scope"] = "ava"
    with pytest.raises(FleetConfigError, match="session_scope collision"):
        load_fleet_config(_write(tmp_path, raw))


def test_rejects_source_repository_inside_state_root(tmp_path):
    raw = _raw(tmp_path)
    raw["source"]["repository"] = str(tmp_path / "state" / "ava" / "source")
    with pytest.raises(FleetConfigError, match="must be disjoint"):
        load_fleet_config(_write(tmp_path, raw))


def test_rejects_relative_paths(tmp_path):
    raw = _raw(tmp_path)
    raw["entities"]["ava"]["workspace"] = "relative/ava"
    with pytest.raises(FleetConfigError, match="must be absolute"):
        load_fleet_config(_write(tmp_path, raw))


def test_rejects_config_version_drift(tmp_path):
    raw = _raw(tmp_path)
    raw["version"] = 2
    with pytest.raises(FleetConfigError, match="unsupported fleet config version"):
        load_fleet_config(_write(tmp_path, raw))


def test_rejects_snapshot_root_nested_with_entity_state(tmp_path):
    raw = _raw(tmp_path)
    raw["snapshots"]["root"] = str(tmp_path / "state" / "ava" / "snapshots")
    with pytest.raises(FleetConfigError, match="managed roots must be disjoint"):
        load_fleet_config(_write(tmp_path, raw))


def test_rejects_unknown_root_field(tmp_path):
    raw = _raw(tmp_path)
    raw["typo_policy"] = {}
    with pytest.raises(FleetConfigError, match="unknown fields"):
        load_fleet_config(_write(tmp_path, raw))


def test_rejects_moving_expected_ref(tmp_path):
    raw = _raw(tmp_path)
    raw["source"]["expected_ref"] = "ava/stable"
    with pytest.raises(FleetConfigError, match="exact 40-character"):
        load_fleet_config(_write(tmp_path, raw))


def test_rejects_lowered_fail_closed_policy(tmp_path):
    raw = _raw(tmp_path)
    raw["policy"]["resume_missing_session"] = "fallback"
    with pytest.raises(FleetConfigError, match="cannot be lowered"):
        load_fleet_config(_write(tmp_path, raw))


def test_rejects_auto_update(tmp_path):
    raw = _raw(tmp_path)
    raw["source"]["auto_update"] = True
    with pytest.raises(FleetConfigError, match="must remain false"):
        load_fleet_config(_write(tmp_path, raw))
