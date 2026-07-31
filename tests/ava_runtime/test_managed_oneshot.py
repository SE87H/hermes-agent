from __future__ import annotations

from types import SimpleNamespace

import pytest

from hermes_cli.ava_runtime import managed_oneshot


def _compatible_run_oneshot(
    prompt,
    model=None,
    provider=None,
    toolsets=None,
    usage_file=None,
):
    return 0


def _compatible_run_agent(
    prompt,
    model=None,
    provider=None,
    toolsets=None,
    use_config_toolsets=True,
):
    return "", {}


def test_upstream_compatibility_accepts_expected_surface():
    upstream = SimpleNamespace(
        run_oneshot=_compatible_run_oneshot,
        _run_agent=_compatible_run_agent,
    )

    managed_oneshot._assert_upstream_compatibility(upstream)


def test_upstream_compatibility_fails_closed_on_signature_drift():
    def changed_run_oneshot(prompt, new_option=None):
        return 0

    upstream = SimpleNamespace(
        run_oneshot=changed_run_oneshot,
        _run_agent=_compatible_run_agent,
    )

    with pytest.raises(RuntimeError, match="signature changed"):
        managed_oneshot._assert_upstream_compatibility(upstream)


def test_parser_uses_narrow_explicit_surface():
    args = managed_oneshot.build_parser().parse_args(
        [
            "--resume",
            "session-1",
            "--no-restore-cwd",
            "--model",
            "model-1",
            "continue work",
        ]
    )

    assert args.resume_session_id == "session-1"
    assert args.continue_named is None
    assert args.continue_last is False
    assert args.restore_cwd is False
    assert args.model == "model-1"
    assert args.prompt == "continue work"


def test_parser_separates_named_and_latest_continue():
    named = managed_oneshot.build_parser().parse_args(
        ["--continue", "AVA planning", "continue work"]
    )
    latest = managed_oneshot.build_parser().parse_args(
        ["--continue-last", "continue work"]
    )

    assert named.continue_named == "AVA planning"
    assert named.continue_last is False
    assert latest.continue_named is None
    assert latest.continue_last is True


def test_parser_rejects_unknown_options():
    with pytest.raises(SystemExit):
        managed_oneshot.build_parser().parse_args(["--skills", "x", "prompt"])


def test_overlay_restores_upstream_run_agent(monkeypatch, tmp_path):
    import hermes_cli.oneshot as upstream

    events = []

    class FakeIdentity:
        workspace = tmp_path

        def activate_workspace(self):
            events.append("activate")

    monkeypatch.setattr(
        managed_oneshot.ManagedIdentity,
        "from_env",
        classmethod(lambda cls: FakeIdentity()),
    )

    original = _compatible_run_agent
    monkeypatch.setattr(upstream, "_run_agent", original)

    def replacement_run_agent(
        prompt,
        model=None,
        provider=None,
        toolsets=None,
        use_config_toolsets=True,
    ):
        return _compatible_run_agent(
            prompt,
            model=model,
            provider=provider,
            toolsets=toolsets,
            use_config_toolsets=use_config_toolsets,
        )

    def fake_run_oneshot(
        prompt,
        model=None,
        provider=None,
        toolsets=None,
        usage_file=None,
    ):
        events.append(("run", prompt, model, provider, toolsets, usage_file))
        assert upstream._run_agent is replacement_run_agent
        return 17

    monkeypatch.setattr(upstream, "run_oneshot", fake_run_oneshot)

    def fake_builder(_upstream, request):
        events.append(("request", request.resume_session_id, request.workspace_key))
        return replacement_run_agent

    monkeypatch.setattr(managed_oneshot, "_build_managed_run_agent", fake_builder)

    rc = managed_oneshot.run_managed_oneshot(
        "prompt",
        resume_session_id="session-1",
        model="model-1",
        usage_file="usage.json",
    )

    assert rc == 17
    assert events[0] == "activate"
    assert ("request", "session-1", str(tmp_path)) in events
    assert upstream._run_agent is original
