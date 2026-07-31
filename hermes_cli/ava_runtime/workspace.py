"""Atomic process-local workspace transitions for managed AVA runtimes.

Finite CLI/one-shot processes may intentionally move their process working
directory.  Hermes also exposes the same workspace through ``TERMINAL_CWD``.
This module is the single write-side seam that keeps those two representations
coherent and publishes neither when the transition fails.

Concurrent gateway/cron hosts must use Hermes' task-local session cwd
ContextVar instead of this process-global operator.
"""

from __future__ import annotations

import os
from pathlib import Path


def activate_process_workspace(
    workspace: str | os.PathLike[str],
    *,
    missing_message: str | None = None,
    enter_message: str | None = None,
) -> Path:
    """Enter *workspace* and publish its canonical path to ``TERMINAL_CWD``.

    The environment is updated only after ``chdir`` succeeds.  If canonical
    resolution fails after entry, the prior process directory and environment
    value are restored before the error is re-raised.
    """

    path = Path(workspace).expanduser()
    if not path.is_absolute():
        raise RuntimeError(f"Managed workspace path must be absolute: {path}")
    if not path.is_dir():
        raise FileNotFoundError(missing_message or f"Workspace is unavailable: {path}")

    previous_cwd = Path.cwd()
    previous_terminal_cwd = os.environ.get("TERMINAL_CWD")
    try:
        os.chdir(path)
        active = Path.cwd().resolve()
        os.environ["TERMINAL_CWD"] = str(active)
        return active
    except OSError as exc:
        try:
            os.chdir(previous_cwd)
        except OSError:
            pass
        if previous_terminal_cwd is None:
            os.environ.pop("TERMINAL_CWD", None)
        else:
            os.environ["TERMINAL_CWD"] = previous_terminal_cwd
        raise RuntimeError(enter_message or f"Failed to enter workspace: {path}") from exc
