"""Managed one-shot launcher with durable identity and workspace closure.

Usage:

    AVA_ENTITY=avaeon-codex \
    HERMES_HOME=/var/lib/ava/hermes/avaeon-codex \
    AVA_WORKSPACE=/srv/ava/workspaces/avaeon-codex \
    python -m hermes_cli.ava_runtime.managed_oneshot \
        --resume SESSION_ID "Continue the work"

This overlay intentionally accepts a narrow option surface. Unknown options are
rejected by argparse instead of being silently discarded.
"""

from __future__ import annotations

import argparse
import inspect
import logging
import os
import sys
from typing import Any

from hermes_cli.ava_runtime.identity import ManagedIdentity
from hermes_cli.ava_runtime.session_context import ResumeRequest, resolve_session_context


_EXPECTED_UPSTREAM_RUN_ONESHOT_PARAMS = {
    "prompt",
    "model",
    "provider",
    "toolsets",
    "usage_file",
}
_EXPECTED_UPSTREAM_RUN_AGENT_PARAMS = {
    "prompt",
    "model",
    "provider",
    "toolsets",
    "use_config_toolsets",
}


def _assert_upstream_compatibility(upstream: Any) -> None:
    run_oneshot_params = set(inspect.signature(upstream.run_oneshot).parameters)
    if run_oneshot_params != _EXPECTED_UPSTREAM_RUN_ONESHOT_PARAMS:
        raise RuntimeError(
            "Upstream run_oneshot signature changed; managed overlay requires review. "
            f"Expected {sorted(_EXPECTED_UPSTREAM_RUN_ONESHOT_PARAMS)}, "
            f"found {sorted(run_oneshot_params)}."
        )

    run_agent_params = set(inspect.signature(upstream._run_agent).parameters)
    if run_agent_params != _EXPECTED_UPSTREAM_RUN_AGENT_PARAMS:
        raise RuntimeError(
            "Upstream _run_agent signature changed; managed overlay requires review. "
            f"Expected {sorted(_EXPECTED_UPSTREAM_RUN_AGENT_PARAMS)}, "
            f"found {sorted(run_agent_params)}."
        )


def _attach_durable_run_metadata(result: dict[str, Any], agent: Any) -> dict[str, Any]:
    """Ensure one-shot usage evidence carries the durable agent identity."""
    result.setdefault("session_id", agent.session_id)
    result.setdefault("model", agent.model)
    result.setdefault("provider", agent.provider)
    return result


def _build_managed_run_agent(upstream: Any, request: ResumeRequest):
    def _managed_run_agent(
        prompt: str,
        model: str | None = None,
        provider: str | None = None,
        toolsets: object = None,
        use_config_toolsets: bool = True,
    ) -> tuple[str, dict]:
        from hermes_cli.config import load_config
        from hermes_cli.fallback_config import get_fallback_chain
        from hermes_cli.models import detect_provider_for_model
        from hermes_cli.runtime_provider import resolve_runtime_provider
        from hermes_cli.tools_config import _get_platform_tools
        from run_agent import AIAgent

        cfg = load_config()
        model_cfg = cfg.get("model") or {}
        if isinstance(model_cfg, str):
            cfg_model = model_cfg
        else:
            cfg_model = model_cfg.get("default") or model_cfg.get("model") or ""

        env_model = os.getenv("HERMES_INFERENCE_MODEL", "").strip()
        effective_model = (model or "").strip() or env_model or cfg_model
        effective_provider = (provider or "").strip() or None
        explicit_base_url_from_alias: str | None = None

        if effective_provider is None and (model or env_model):
            explicit_model = (model or "").strip() or env_model
            if explicit_model:
                try:
                    from hermes_cli import model_switch as _ms

                    _ms._ensure_direct_aliases()
                    direct = _ms.DIRECT_ALIASES.get(explicit_model.strip().lower())
                except Exception:
                    direct = None
                if direct is not None:
                    effective_model = direct.model
                    effective_provider = direct.provider
                    if direct.base_url:
                        explicit_base_url_from_alias = direct.base_url.rstrip("/")
                else:
                    cfg_provider = ""
                    if isinstance(model_cfg, dict):
                        cfg_provider = str(model_cfg.get("provider") or "").strip().lower()
                    current_provider = (
                        cfg_provider
                        or os.getenv("HERMES_INFERENCE_PROVIDER", "").strip().lower()
                        or "auto"
                    )
                    detected = detect_provider_for_model(explicit_model, current_provider)
                    if detected:
                        effective_provider, effective_model = detected

        runtime = resolve_runtime_provider(
            requested=effective_provider,
            target_model=effective_model or None,
            explicit_base_url=explicit_base_url_from_alias,
        )

        toolsets_list = upstream._normalize_toolsets(toolsets)
        if toolsets_list is None and use_config_toolsets:
            toolsets_list = sorted(_get_platform_tools(cfg, "cli"))

        session_db = upstream._create_session_db_for_oneshot()
        agent = None
        try:
            context = resolve_session_context(session_db, request)
            session_id = context.session_id if context else None
            history = context.conversation_history if context else None
            fallback_chain = get_fallback_chain(cfg)

            agent = AIAgent(
                api_key=runtime.get("api_key"),
                base_url=runtime.get("base_url"),
                provider=runtime.get("provider"),
                requested_provider=runtime.get("requested_provider"),
                api_mode=runtime.get("api_mode"),
                model=effective_model,
                enabled_toolsets=toolsets_list,
                quiet_mode=True,
                platform="cli",
                session_db=session_db,
                session_id=session_id,
                credential_pool=runtime.get("credential_pool"),
                fallback_model=fallback_chain or None,
                clarify_callback=upstream._oneshot_clarify_callback,
            )
            agent.suppress_status_output = True
            agent.stream_delta_callback = None
            agent.tool_gen_callback = None

            result = agent.run_conversation(prompt, conversation_history=history)
            _attach_durable_run_metadata(result, agent)
            return result.get("final_response") or "", result
        finally:
            if agent is not None:
                try:
                    session_messages = getattr(agent, "_session_messages", None)
                    if isinstance(session_messages, list):
                        agent.shutdown_memory_provider(session_messages)
                    else:
                        agent.shutdown_memory_provider()
                except Exception:
                    logging.debug("managed oneshot memory cleanup failed", exc_info=True)
                try:
                    agent.close()
                except Exception:
                    logging.debug("managed oneshot agent cleanup failed", exc_info=True)
            if session_db is not None:
                try:
                    session_db.close()
                except Exception:
                    logging.debug("managed oneshot session store cleanup failed", exc_info=True)

    return _managed_run_agent


def run_managed_oneshot(
    prompt: str,
    *,
    resume_session_id: str | None = None,
    continue_last: bool | str | None = None,
    restore_cwd: bool = True,
    require_recorded_cwd: bool = True,
    allow_global_fallback: bool = False,
    model: str | None = None,
    provider: str | None = None,
    toolsets: object = None,
    usage_file: str | None = None,
) -> int:
    from hermes_cli import oneshot as upstream

    _assert_upstream_compatibility(upstream)

    identity = ManagedIdentity.from_env()
    identity.activate_workspace()
    request = ResumeRequest(
        resume_session_id=resume_session_id,
        continue_last=continue_last,
        restore_cwd=restore_cwd,
        require_recorded_cwd=require_recorded_cwd,
        workspace_key=str(identity.workspace),
        allow_global_fallback=allow_global_fallback,
    )

    original_run_agent = upstream._run_agent
    upstream._run_agent = _build_managed_run_agent(upstream, request)
    try:
        return upstream.run_oneshot(
            prompt,
            model=model,
            provider=provider,
            toolsets=toolsets,
            usage_file=usage_file,
        )
    finally:
        upstream._run_agent = original_run_agent


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--resume", "-r", dest="resume_session_id")
    target.add_argument(
        "--continue",
        "-c",
        dest="continue_named",
        metavar="ID_OR_TITLE",
        help="Continue an explicit session ID or title.",
    )
    target.add_argument(
        "--continue-last",
        action="store_true",
        help="Continue the latest session in AVA_WORKSPACE only.",
    )
    parser.add_argument("--no-restore-cwd", action="store_false", dest="restore_cwd")
    parser.add_argument(
        "--allow-missing-recorded-cwd",
        action="store_false",
        dest="require_recorded_cwd",
    )
    parser.add_argument(
        "--allow-global-fallback",
        action="store_true",
        help="Allow --continue-last to leave the current workspace within this entity home.",
    )
    parser.add_argument("--model")
    parser.add_argument("--provider")
    parser.add_argument("--toolsets")
    parser.add_argument("--usage-file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    continue_last: bool | str | None = args.continue_named
    if args.continue_last:
        continue_last = True
    return run_managed_oneshot(
        args.prompt,
        resume_session_id=args.resume_session_id,
        continue_last=continue_last,
        restore_cwd=args.restore_cwd,
        require_recorded_cwd=args.require_recorded_cwd,
        allow_global_fallback=args.allow_global_fallback,
        model=args.model,
        provider=args.provider,
        toolsets=args.toolsets,
        usage_file=args.usage_file,
    )


def _entrypoint() -> None:
    try:
        rc = main()
    except KeyboardInterrupt:
        rc = 130
    except BaseException as exc:  # noqa: BLE001
        print(f"managed Hermes oneshot failed: {exc}", file=sys.stderr)
        rc = 1

    try:
        from hermes_cli.main import _cleanup_oneshot_runtime

        _cleanup_oneshot_runtime()
    except Exception:
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(rc if isinstance(rc, int) else 1)


if __name__ == "__main__":
    _entrypoint()
