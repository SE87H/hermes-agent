from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
DOCTOR_PATH = ROOT / "scripts" / "ava_runtime" / "doctor.py"


def _load_doctor_module():
    spec = importlib.util.spec_from_file_location("ava_runtime_doctor", DOCTOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def doctor_mod():
    return _load_doctor_module()


def _fake_clean_git(_repo: Path, *args: str):
    if args == ("rev-parse", "HEAD"):
        return 0, "abc123", ""
    if args == ("branch", "--show-current"):
        return 0, "ava/stable", ""
    if args[:2] == ("rev-parse", "--verify"):
        return 0, "abc123", ""
    if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
        return 0, "", ""
    raise AssertionError(f"unexpected git invocation: {args}")


def test_scoped_runtime_passes(monkeypatch, tmp_path, capsys, doctor_mod):
    repo = tmp_path / "repo"
    workspace = tmp_path / "workspaces" / "ava"
    hermes_home = tmp_path / "state" / "ava"
    repo.mkdir()
    workspace.mkdir(parents=True)
    hermes_home.mkdir(parents=True)
    (hermes_home / "state.db").touch()

    monkeypatch.setattr(doctor_mod, "_run_git", _fake_clean_git)

    rc = doctor_mod.main(
        [
            "--entity",
            "ava",
            "--repo",
            str(repo),
            "--workspace",
            str(workspace),
            "--hermes-home",
            str(hermes_home),
            "--expected-ref",
            "ava/stable",
            "--require-clean",
            "--require-state-db",
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert payload["status"] == "pass"
    assert not [check for check in payload["checks"] if check["status"] == "error"]


def test_unscoped_hermes_home_fails(monkeypatch, tmp_path, capsys, doctor_mod):
    repo = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    hermes_home = tmp_path / "state" / "shared"
    repo.mkdir()
    workspace.mkdir()
    hermes_home.mkdir(parents=True)

    monkeypatch.setattr(doctor_mod, "_run_git", _fake_clean_git)

    rc = doctor_mod.main(
        [
            "--entity",
            "aeon",
            "--repo",
            str(repo),
            "--workspace",
            str(workspace),
            "--hermes-home",
            str(hermes_home),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["status"] == "fail"
    assert any(
        check["name"] == "isolation.hermes_home" and check["status"] == "error"
        for check in payload["checks"]
    )


def test_workspace_inside_state_root_fails(monkeypatch, tmp_path, capsys, doctor_mod):
    repo = tmp_path / "repo"
    hermes_home = tmp_path / "state" / "avaeon-codex"
    workspace = hermes_home / "workspace"
    repo.mkdir()
    workspace.mkdir(parents=True)

    monkeypatch.setattr(doctor_mod, "_run_git", _fake_clean_git)

    rc = doctor_mod.main(
        [
            "--entity",
            "avaeon-codex",
            "--repo",
            str(repo),
            "--workspace",
            str(workspace),
            "--hermes-home",
            str(hermes_home),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert any(
        check["name"] == "isolation.workspace" and check["status"] == "error"
        for check in payload["checks"]
    )


def test_missing_entity_fails(monkeypatch, tmp_path, capsys, doctor_mod):
    repo = tmp_path / "repo"
    workspace = tmp_path / "workspace"
    hermes_home = tmp_path / "state" / "ava"
    repo.mkdir()
    workspace.mkdir()
    hermes_home.mkdir(parents=True)

    monkeypatch.delenv("AVA_ENTITY", raising=False)
    monkeypatch.setattr(doctor_mod, "_run_git", _fake_clean_git)

    rc = doctor_mod.main(
        [
            "--repo",
            str(repo),
            "--workspace",
            str(workspace),
            "--hermes-home",
            str(hermes_home),
            "--json",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert any(
        check["name"] == "identity.entity" and check["status"] == "error"
        for check in payload["checks"]
    )
