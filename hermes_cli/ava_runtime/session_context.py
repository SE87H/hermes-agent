"""Fail-closed durable session resolution for managed Hermes runtimes."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ResumeRequest:
    """Identity-bearing request for one durable session.

    Managed runtimes forbid a cross-workspace global-most-recent fallback by
    default. Callers may opt in only when their HERMES_HOME is already isolated
    and the broader selection is intentional.
    """

    resume_session_id: str | None = None
    continue_last: bool | str | None = None
    restore_cwd: bool = True
    require_recorded_cwd: bool = True
    source: str = "cli"
    workspace_key: str | None = None
    allow_global_fallback: bool = False

    @property
    def requested(self) -> bool:
        return bool(self.resume_session_id or self.continue_last)


@dataclass(frozen=True)
class ResolvedSessionContext:
    session_id: str
    conversation_history: list[dict[str, Any]]
    recorded_cwd: str | None
    selection: str


def resolve_workspace_key(cwd: str | os.PathLike[str] | None = None) -> str:
    """Return the current Git root, or the absolute working directory."""

    base = Path(cwd or os.getcwd()).expanduser().resolve()
    try:
        result = subprocess.run(
            ["git", "-C", str(base), "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return str(base)
    if result.returncode == 0 and result.stdout.strip():
        return str(Path(result.stdout.strip()).expanduser().resolve())
    return str(base)


def _resolve_target(session_db: Any, request: ResumeRequest) -> tuple[str, str]:
    explicit = str(request.resume_session_id or "").strip()
    if explicit:
        return explicit, "explicit-resume"

    if isinstance(request.continue_last, str):
        title_or_id = request.continue_last.strip()
        if title_or_id:
            return title_or_id, "named-continue"

    if not request.continue_last:
        raise ValueError("No resume or continue target was requested.")

    workspace_key = request.workspace_key or resolve_workspace_key()
    recent = session_db.search_sessions(
        source=request.source,
        limit=1,
        workspace_key=workspace_key,
    )
    if recent:
        return str(recent[0]["id"]), "workspace-latest"

    if request.allow_global_fallback:
        recent = session_db.search_sessions(source=request.source, limit=1)
        if recent:
            return str(recent[0]["id"]), "entity-global-latest"

    raise ValueError(
        "No previous session exists in the selected workspace; managed runtime "
        "refuses a global-most-recent fallback. Pass an explicit session ID/title."
    )


def _resolve_existing_session(session_db: Any, target: str) -> tuple[str, dict[str, Any]]:
    session_meta = session_db.get_session(target)
    resolved_target = target

    if not session_meta:
        title_match = session_db.resolve_session_by_title(target)
        if title_match:
            resolved_target = str(title_match)
            session_meta = session_db.get_session(resolved_target)

    if not session_meta:
        raise ValueError(f"Session not found: {target}")

    canonical_id = session_db.resolve_resume_session_id(resolved_target) or resolved_target
    canonical_id = str(canonical_id)
    if canonical_id != resolved_target:
        session_meta = session_db.get_session(canonical_id)
    if not session_meta:
        raise ValueError(f"Canonical session not found: {canonical_id}")

    return canonical_id, session_meta


def _restore_recorded_cwd(session_meta: dict[str, Any], request: ResumeRequest) -> str | None:
    saved_cwd = str(session_meta.get("cwd") or "").strip()
    if not request.restore_cwd:
        return saved_cwd or None

    if not saved_cwd:
        if request.require_recorded_cwd:
            raise RuntimeError("Resumed session has no recorded working directory.")
        return None

    path = Path(saved_cwd).expanduser()
    if not path.is_dir():
        raise FileNotFoundError(f"Recorded session working directory is unavailable: {path}")
    try:
        os.chdir(path)
    except OSError as exc:
        raise RuntimeError(
            f"Failed to restore recorded session working directory: {path}"
        ) from exc
    return str(path.resolve())


def resolve_session_context(
    session_db: Any,
    request: ResumeRequest,
) -> ResolvedSessionContext | None:
    """Resolve exact durable identity, history, and workspace or fail visibly.

    The session is reopened only after history and workspace restoration have
    succeeded. A failed transition therefore leaves the durable session state
    untouched.
    """

    if not request.requested:
        return None
    if session_db is None:
        raise RuntimeError("Session database unavailable; cannot resume managed runtime.")

    target, selection = _resolve_target(session_db, request)
    session_id, session_meta = _resolve_existing_session(session_db, target)

    conversation_history, _display_history = session_db.get_resume_conversations(session_id)
    history = [
        message
        for message in conversation_history
        if isinstance(message, dict) and message.get("role") != "session_meta"
    ]

    recorded_cwd = _restore_recorded_cwd(session_meta, request)
    session_db.reopen_session(session_id)

    return ResolvedSessionContext(
        session_id=session_id,
        conversation_history=history,
        recorded_cwd=recorded_cwd,
        selection=selection,
    )
