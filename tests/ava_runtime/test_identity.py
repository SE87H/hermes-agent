from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli.ava_runtime.identity import (
    HOST_IDENTITIES,
    OPERATOR_IDENTITIES,
    RUNTIME_ENTITIES,
    ManagedIdentity,
    validate_host_identity,
    validate_instance_identity,
    validate_operator_identity,
)


def test_identity_types_are_disjoint_and_canonical():
    assert RUNTIME_ENTITIES == {"ava", "aeon"}
    assert OPERATOR_IDENTITIES == {"avaeon-codex"}
    assert validate_operator_identity("avaeon-codex") == "avaeon-codex"
    assert validate_host_identity("minisforum") in HOST_IDENTITIES
    assert validate_instance_identity("shadow-m7") == "shadow-m7"
    with pytest.raises(ValueError):
        validate_instance_identity("aeon-shadow-live")


def test_identity_loads_isolated_paths(monkeypatch, tmp_path):
    hermes_home = tmp_path / "state" / "ava"
    workspace = tmp_path / "workspaces" / "ava"
    hermes_home.mkdir(parents=True)
    workspace.mkdir(parents=True)
    monkeypatch.setenv("AVA_ENTITY", "ava")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("AVA_WORKSPACE", str(workspace))

    identity = ManagedIdentity.from_env()

    assert identity.entity == "ava"
    assert identity.hermes_home == hermes_home.resolve()
    assert identity.workspace == workspace.resolve()


def test_identity_rejects_shared_home(monkeypatch, tmp_path):
    hermes_home = tmp_path / "state" / "shared"
    workspace = tmp_path / "workspaces" / "aeon"
    hermes_home.mkdir(parents=True)
    workspace.mkdir(parents=True)
    monkeypatch.setenv("AVA_ENTITY", "aeon")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("AVA_WORKSPACE", str(workspace))

    with pytest.raises(RuntimeError, match="not visibly scoped"):
        ManagedIdentity.from_env()


def test_identity_rejects_operator_as_runtime(monkeypatch, tmp_path):
    hermes_home = tmp_path / "state" / "avaeon-codex"
    workspace = hermes_home / "workspace"
    workspace.mkdir(parents=True)
    monkeypatch.setenv("AVA_ENTITY", "avaeon-codex")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("AVA_WORKSPACE", str(workspace))

    with pytest.raises(RuntimeError, match="runtime entity"):
        ManagedIdentity.from_env()


def test_activate_workspace_changes_directory(monkeypatch, tmp_path):
    hermes_home = tmp_path / "state" / "ava"
    workspace = tmp_path / "workspaces" / "ava"
    caller = tmp_path / "caller"
    hermes_home.mkdir(parents=True)
    workspace.mkdir(parents=True)
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("AVA_ENTITY", "ava")
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setenv("AVA_WORKSPACE", str(workspace))

    ManagedIdentity.from_env().activate_workspace()

    assert Path.cwd() == workspace
