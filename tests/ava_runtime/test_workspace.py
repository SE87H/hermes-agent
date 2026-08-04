from __future__ import annotations

import os
from pathlib import Path

import pytest

from hermes_cli.ava_runtime import workspace as workspace_mod
from hermes_cli.ava_runtime.workspace import activate_process_workspace


@pytest.fixture(autouse=True)
def _restore_process_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    """Production transitions must not leak between tests."""
    monkeypatch.chdir(Path.cwd())


def test_activate_process_workspace_synchronizes_both_carriers(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    caller = tmp_path / "caller"
    target = tmp_path / "target"
    caller.mkdir()
    target.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("TERMINAL_CWD", str(tmp_path / "stale"))

    active = activate_process_workspace(target)

    assert active == target.resolve()
    assert Path.cwd() == target.resolve()
    assert os.environ["TERMINAL_CWD"] == str(target.resolve())


def test_missing_workspace_leaves_process_context_untouched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("TERMINAL_CWD", "/previous")

    with pytest.raises(FileNotFoundError, match="Workspace is unavailable"):
        activate_process_workspace(tmp_path / "missing")

    assert Path.cwd() == caller.resolve()
    assert os.environ["TERMINAL_CWD"] == "/previous"


def test_failed_chdir_leaves_process_context_untouched(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    caller = tmp_path / "caller"
    target = tmp_path / "target"
    caller.mkdir()
    target.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("TERMINAL_CWD", "/previous")

    real_chdir = os.chdir
    calls = 0

    def fail_initial_chdir_then_allow_rollback(path: object) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("blocked")
        real_chdir(path)

    monkeypatch.setattr(
        workspace_mod.os,
        "chdir",
        fail_initial_chdir_then_allow_rollback,
    )

    with pytest.raises(RuntimeError, match="Failed to enter workspace"):
        activate_process_workspace(target)

    assert Path.cwd() == caller.resolve()
    assert os.environ["TERMINAL_CWD"] == "/previous"


def test_post_entry_failure_rolls_back_cwd_and_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    caller = tmp_path / "caller"
    target = tmp_path / "target"
    caller.mkdir()
    target.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("TERMINAL_CWD", "/previous")

    real_cwd = Path.cwd
    calls = 0

    def flaky_cwd(_cls) -> Path:
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_cwd()
        raise OSError("cwd vanished")

    monkeypatch.setattr(workspace_mod.Path, "cwd", classmethod(flaky_cwd))

    with pytest.raises(RuntimeError, match="Failed to enter workspace"):
        activate_process_workspace(target)

    assert Path(os.getcwd()).resolve() == caller.resolve()
    assert os.environ["TERMINAL_CWD"] == "/previous"


def test_failed_rollback_keeps_environment_aligned_to_surviving_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    caller = tmp_path / "caller"
    target = tmp_path / "target"
    caller.mkdir()
    target.mkdir()
    monkeypatch.chdir(caller)
    monkeypatch.setenv("TERMINAL_CWD", "/previous")

    real_chdir = os.chdir
    chdir_calls = 0

    def enter_then_block_rollback(path: object) -> None:
        nonlocal chdir_calls
        chdir_calls += 1
        if chdir_calls == 1:
            real_chdir(path)
            return
        raise OSError("rollback blocked")

    real_cwd = Path.cwd
    cwd_calls = 0

    def fail_canonicalization_once(_cls) -> Path:
        nonlocal cwd_calls
        cwd_calls += 1
        if cwd_calls == 1:
            return real_cwd()
        if cwd_calls == 2:
            raise OSError("canonicalization failed")
        return real_cwd()

    monkeypatch.setattr(workspace_mod.os, "chdir", enter_then_block_rollback)
    monkeypatch.setattr(
        workspace_mod.Path,
        "cwd",
        classmethod(fail_canonicalization_once),
    )

    with pytest.raises(RuntimeError, match="rollback .* also failed"):
        activate_process_workspace(target)

    assert Path(os.getcwd()).resolve() == target.resolve()
    assert os.environ["TERMINAL_CWD"] == str(target.resolve())