from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_cli.ava_runtime.identity import ManagedIdentity
from hermes_cli.ava_runtime.session_context import ResumeRequest, _restore_recorded_cwd


@pytest.fixture(autouse=True)
def _restore_process_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production workspace transitions must not leak between tests."""
    monkeypatch.chdir(Path.cwd())


def test_managed_identity_synchronizes_process_and_terminal_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "ava" / "state"
    workspace.mkdir()
    home.mkdir(parents=True)
    identity = ManagedIdentity(entity="ava", hermes_home=home, workspace=workspace.resolve())

    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path / "stale"))
    identity.activate_workspace()

    assert Path.cwd() == workspace.resolve()
    assert os.environ["TERMINAL_CWD"] == str(workspace.resolve())


def test_managed_identity_does_not_publish_terminal_workspace_when_chdir_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    home = tmp_path / "ava" / "state"
    workspace.mkdir()
    home.mkdir(parents=True)
    identity = ManagedIdentity(entity="ava", hermes_home=home, workspace=workspace.resolve())
    monkeypatch.setenv("TERMINAL_CWD", "/previous")

    real_chdir = os.chdir
    calls = 0

    def fail_initial_chdir_then_allow_rollback(path: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("blocked")
        real_chdir(path)

    monkeypatch.setattr(os, "chdir", fail_initial_chdir_then_allow_rollback)

    with pytest.raises(RuntimeError, match="Cannot enter AVA_WORKSPACE"):
        identity.activate_workspace()

    assert os.environ["TERMINAL_CWD"] == "/previous"


def test_resume_synchronizes_terminal_workspace_after_successful_chdir(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "session-workspace"
    workspace.mkdir()
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path / "stale"))

    restored = _restore_recorded_cwd(
        {"cwd": str(workspace)},
        ResumeRequest(resume_session_id="session-1"),
    )

    assert restored == str(workspace.resolve())
    assert Path.cwd() == workspace.resolve()
    assert os.environ["TERMINAL_CWD"] == str(workspace.resolve())


def test_resume_failure_keeps_previous_terminal_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing"
    monkeypatch.setenv("TERMINAL_CWD", "/previous")

    with pytest.raises(FileNotFoundError, match="working directory is unavailable"):
        _restore_recorded_cwd(
            {"cwd": str(missing)},
            ResumeRequest(resume_session_id="session-1"),
        )

    assert os.environ["TERMINAL_CWD"] == "/previous"


def test_resume_opt_out_preserves_existing_runtime_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "recorded"
    workspace.mkdir()
    original_cwd = Path.cwd()
    monkeypatch.setenv("TERMINAL_CWD", "/intentional-current-workspace")

    restored = _restore_recorded_cwd(
        {"cwd": str(workspace)},
        ResumeRequest(resume_session_id="session-1", restore_cwd=False),
    )

    assert restored == str(workspace)
    assert Path.cwd() == original_cwd
    assert os.environ["TERMINAL_CWD"] == "/intentional-current-workspace"