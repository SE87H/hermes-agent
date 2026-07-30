from __future__ import annotations

from pathlib import Path

import pytest

from hermes_cli.ava_runtime.session_context import ResumeRequest, resolve_session_context


class FakeSessionDB:
    def __init__(self, sessions=None, histories=None, workspace_recent=None, global_recent=None):
        self.sessions = sessions or {}
        self.histories = histories or {}
        self.workspace_recent = workspace_recent or []
        self.global_recent = global_recent or []
        self.title_matches = {}
        self.canonical = {}
        self.events = []
        self.searches = []

    def get_session(self, session_id):
        self.events.append(("get", session_id))
        return self.sessions.get(session_id)

    def resolve_session_by_title(self, title):
        self.events.append(("title", title))
        return self.title_matches.get(title)

    def resolve_resume_session_id(self, session_id):
        self.events.append(("canonical", session_id))
        return self.canonical.get(session_id, session_id)

    def get_resume_conversations(self, session_id):
        self.events.append(("history", session_id))
        return self.histories.get(session_id, ([], []))

    def reopen_session(self, session_id):
        self.events.append(("reopen", session_id))

    def search_sessions(self, *, source, limit, workspace_key=None):
        self.searches.append((source, limit, workspace_key))
        if workspace_key is not None:
            return self.workspace_recent
        return self.global_recent


def test_explicit_resume_uses_canonical_tip_and_restores_cwd(monkeypatch, tmp_path):
    saved_cwd = tmp_path / "workspace"
    saved_cwd.mkdir()
    caller_cwd = tmp_path / "caller"
    caller_cwd.mkdir()
    monkeypatch.chdir(caller_cwd)

    db = FakeSessionDB(
        sessions={
            "root": {"id": "root", "cwd": str(saved_cwd)},
            "tip": {"id": "tip", "cwd": str(saved_cwd)},
        },
        histories={
            "tip": (
                [
                    {"role": "session_meta", "content": "internal"},
                    {"role": "user", "content": "prior"},
                ],
                [],
            )
        },
    )
    db.canonical["root"] = "tip"

    context = resolve_session_context(
        db,
        ResumeRequest(resume_session_id="root"),
    )

    assert context is not None
    assert context.session_id == "tip"
    assert context.conversation_history == [{"role": "user", "content": "prior"}]
    assert context.recorded_cwd == str(saved_cwd.resolve())
    assert context.selection == "explicit-resume"
    assert Path.cwd() == saved_cwd
    assert db.events[-1] == ("reopen", "tip")


def test_named_resume_resolves_title(tmp_path):
    saved_cwd = tmp_path / "workspace"
    saved_cwd.mkdir()
    db = FakeSessionDB(
        sessions={"session-1": {"id": "session-1", "cwd": str(saved_cwd)}},
        histories={"session-1": ([{"role": "user", "content": "prior"}], [])},
    )
    db.title_matches["AVA planning"] = "session-1"

    context = resolve_session_context(
        db,
        ResumeRequest(resume_session_id="AVA planning", restore_cwd=False),
    )

    assert context is not None
    assert context.session_id == "session-1"
    assert context.recorded_cwd == str(saved_cwd)


def test_bare_continue_refuses_cross_workspace_global_fallback(tmp_path):
    db = FakeSessionDB(global_recent=[{"id": "other-project"}])

    with pytest.raises(ValueError, match="refuses a global-most-recent fallback"):
        resolve_session_context(
            db,
            ResumeRequest(continue_last=True, workspace_key=str(tmp_path)),
        )

    assert db.searches == [("cli", 1, str(tmp_path))]
    assert ("reopen", "other-project") not in db.events


def test_bare_continue_can_use_explicit_entity_global_fallback(tmp_path):
    saved_cwd = tmp_path / "workspace"
    saved_cwd.mkdir()
    db = FakeSessionDB(
        sessions={"entity-latest": {"id": "entity-latest", "cwd": str(saved_cwd)}},
        histories={"entity-latest": ([], [])},
        global_recent=[{"id": "entity-latest"}],
    )

    context = resolve_session_context(
        db,
        ResumeRequest(
            continue_last=True,
            workspace_key=str(tmp_path / "missing-workspace"),
            allow_global_fallback=True,
            restore_cwd=False,
        ),
    )

    assert context is not None
    assert context.session_id == "entity-latest"
    assert context.selection == "entity-global-latest"
    assert len(db.searches) == 2


def test_missing_recorded_cwd_fails_before_reopen():
    db = FakeSessionDB(
        sessions={"session-1": {"id": "session-1", "cwd": ""}},
        histories={"session-1": ([], [])},
    )

    with pytest.raises(RuntimeError, match="no recorded working directory"):
        resolve_session_context(db, ResumeRequest(resume_session_id="session-1"))

    assert ("reopen", "session-1") not in db.events


def test_restore_opt_out_allows_missing_recorded_cwd():
    db = FakeSessionDB(
        sessions={"session-1": {"id": "session-1", "cwd": ""}},
        histories={"session-1": ([{"role": "user", "content": "prior"}], [])},
    )

    context = resolve_session_context(
        db,
        ResumeRequest(
            resume_session_id="session-1",
            restore_cwd=False,
            require_recorded_cwd=False,
        ),
    )

    assert context is not None
    assert context.recorded_cwd is None
    assert db.events[-1] == ("reopen", "session-1")


def test_missing_database_fails_closed():
    with pytest.raises(RuntimeError, match="Session database unavailable"):
        resolve_session_context(None, ResumeRequest(resume_session_id="session-1"))
