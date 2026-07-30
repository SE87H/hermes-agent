from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
FLEET_PATH = ROOT / "scripts" / "ava_runtime" / "fleet.py"


def _load_module():
    name = "ava_runtime_fleet_cli_test"
    spec = importlib.util.spec_from_file_location(name, FLEET_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _write_config(tmp_path: Path) -> Path:
    raw = {
        "version": 1,
        "snapshots": {"root": str(tmp_path / "backups")},
        "promotion": {"upstream_snapshot": "ava/upstream-2026-07-30", "staging": "ava/staging", "stable": "ava/stable", "require_shadow_validation": True, "require_rollback_ref": True},
        "policy": {"explicit_hermes_home": "required", "resume_missing_session": "fail", "resume_missing_workspace": "fail", "resume_ambiguous_lineage": "fail", "restore_recorded_workspace": True, "allow_restore_workspace_opt_out": True, "global_most_recent_session": "forbidden_for_managed_entities", "terminal_backend_failure": "fail", "deployment_ref": "pinned_commit_or_stable_tag", "secrets_in_repository": "forbidden"},
        "source": {"repository": str(tmp_path / "source"), "expected_ref": "cccccccccccccccccccccccccccccccccccccccc", "require_clean_checkout": True, "auto_update": False},
        "entities": {},
    }
    for entity in ("ava", "aeon", "avaeon-codex"):
        raw["entities"][entity] = {"hermes_home": str(tmp_path / "state" / entity), "workspace": str(tmp_path / "workspaces" / entity), "profile": entity, "session_scope": entity}
    path = tmp_path / "fleet.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return path


def test_validate_outputs_hash_and_entities(tmp_path, capsys):
    module = _load_module()
    rc = module.main(["--config", str(_write_config(tmp_path)), "validate"])
    output = capsys.readouterr().out
    assert rc == 0
    assert "STATUS_CLOSURE=PASS" in output
    assert "entities=aeon,ava,avaeon-codex" in output


def test_env_json_contains_only_non_secret_runtime_bindings(tmp_path, capsys):
    module = _load_module()
    rc = module.main(["--config", str(_write_config(tmp_path)), "env", "ava", "--format", "json"])
    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["AVA_ENTITY"] == "ava"
    assert payload["AVA_HERMES_EXPECTED_REF"] == "cccccccccccccccccccccccccccccccccccccccc"
    assert not any("KEY" in key or "TOKEN" in key for key in payload)


def test_doctor_all_runs_each_entity_with_isolated_environment(tmp_path, monkeypatch):
    module = _load_module()
    config = _write_config(tmp_path)
    events = []
    monkeypatch.setattr(module, "_run_child", lambda command, *, env, cwd: events.append((command, env.copy(), cwd)) or 0)
    rc = module.main(["--config", str(config), "doctor", "all", "--require-state-db"])
    assert rc == 0
    assert [event[1]["AVA_ENTITY"] for event in events] == ["aeon", "ava", "avaeon-codex"]
    assert len({event[1]["HERMES_HOME"] for event in events}) == 3
    assert all("--require-state-db" in event[0] and "--require-clean" in event[0] for event in events)


def test_smoke_defaults_to_disposable_state(tmp_path, monkeypatch):
    module = _load_module()
    captured = {}
    monkeypatch.setattr(module, "_run_child", lambda command, *, env, cwd: captured.update(command=command, env=env, cwd=cwd) or 0)
    rc = module.main(["--config", str(_write_config(tmp_path)), "smoke", "avaeon-codex"])
    assert rc == 0
    assert "--hermes-home" not in captured["command"]
    assert captured["env"]["AVA_ENTITY"] == "avaeon-codex"


def test_smoke_live_state_is_explicit(tmp_path, monkeypatch):
    module = _load_module()
    captured = {}
    monkeypatch.setattr(module, "_run_child", lambda command, *, env, cwd: captured.update(command=command) or 0)
    rc = module.main(["--config", str(_write_config(tmp_path)), "smoke", "ava", "--live-state"])
    assert rc == 0
    index = captured["command"].index("--hermes-home")
    assert captured["command"][index + 1].endswith("/state/ava")


def test_oneshot_uses_managed_launcher_and_entity_workspace(tmp_path, monkeypatch):
    module = _load_module()
    config = _write_config(tmp_path)
    workspace = tmp_path / "workspaces" / "aeon"
    workspace.mkdir(parents=True)
    captured = {}
    monkeypatch.setattr(module, "_run_child", lambda command, *, env, cwd: captured.update(command=command, env=env, cwd=cwd) or 0)
    rc = module.main(["--config", str(config), "oneshot", "aeon", "--", "--resume", "session-1", "continue the work"])
    assert rc == 0
    assert captured["command"][1:4] == ["-m", "hermes_cli.ava_runtime.managed_oneshot", "--resume"]
    assert captured["env"]["AVA_ENTITY"] == "aeon"
    assert captured["cwd"] == workspace.resolve()


def test_rejected_config_never_launches_child(tmp_path, monkeypatch, capsys):
    module = _load_module()
    config = _write_config(tmp_path)
    raw = yaml.safe_load(config.read_text(encoding="utf-8"))
    raw["entities"]["aeon"]["session_scope"] = "ava"
    config.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    launched = False
    def fake_run(*args, **kwargs):
        nonlocal launched
        launched = True
        return 0
    monkeypatch.setattr(module, "_run_child", fake_run)
    assert module.main(["--config", str(config), "doctor", "all"]) == 2
    assert launched is False
    assert "configuration rejected" in capsys.readouterr().err


def test_snapshot_all_uses_configured_backup_root(tmp_path, monkeypatch):
    module = _load_module()
    config = _write_config(tmp_path)
    events = []
    monkeypatch.setattr(module, "_run_child", lambda command, *, env, cwd: events.append((command, env.copy())) or 0)
    assert module.main(["--config", str(config), "snapshot", "all"]) == 0
    assert [event[1]["AVA_ENTITY"] for event in events] == ["aeon", "ava", "avaeon-codex"]
    for command, _env in events:
        root_index = command.index("--output-root")
        assert command[root_index + 1] == str((tmp_path / "backups").resolve())
