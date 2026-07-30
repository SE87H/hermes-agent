#!/usr/bin/env python3
"""Black-box smoke test for durable one-shot session identity.

The test performs two real Hermes invocations under an isolated HERMES_HOME.
It requires a working model/provider configuration. No credential values are
read or printed by this script.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import sqlite3
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Invocation:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class SmokeResult:
    status: str
    session_id: str | None
    first_session_id: str | None
    second_session_id: str | None
    context_restored: bool
    stable_session_id: bool
    session_count_before_resume: int | None
    session_count_after_resume: int | None
    no_session_fork: bool | None
    hermes_home: str
    workspace: str
    failure: str | None = None


def _run(command: list[str], *, env: dict[str, str], cwd: Path, timeout: int) -> Invocation:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return Invocation(command, 124, exc.stdout or "", exc.stderr or "timed out")
    except OSError as exc:
        return Invocation(command, 127, "", str(exc))
    return Invocation(command, result.returncode, result.stdout, result.stderr)


def _read_usage(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"cannot read usage file {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"usage file is not a JSON object: {path}")
    return payload


def _session_count(state_db: Path) -> int | None:
    if not state_db.is_file():
        return None
    try:
        with sqlite3.connect(f"file:{state_db}?mode=ro", uri=True, timeout=5) as conn:
            row = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()
    except (sqlite3.Error, OSError):
        return None
    return int(row[0]) if row else None


def _base_command(args: argparse.Namespace) -> list[str]:
    command = shlex.split(args.hermes_command)
    if not command:
        raise ValueError("--hermes-command cannot be empty")
    if args.model:
        command.extend(["--model", args.model])
    if args.provider:
        command.extend(["--provider", args.provider])
    if args.toolsets:
        command.extend(["--toolsets", args.toolsets])
    return command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hermes-command", default="hermes")
    parser.add_argument("--model")
    parser.add_argument("--provider")
    parser.add_argument("--toolsets")
    parser.add_argument("--hermes-home")
    parser.add_argument("--workspace")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--keep-temporary-home", action="store_true")
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _emit(result: SmokeResult, *, json_output: bool) -> None:
    if json_output:
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
        return
    print(f"STATUS_CLOSURE={result.status.upper()}")
    print(f"session_id={result.session_id or ''}")
    print(f"context_restored={str(result.context_restored).lower()}")
    print(f"stable_session_id={str(result.stable_session_id).lower()}")
    if result.no_session_fork is not None:
        print(f"no_session_fork={str(result.no_session_fork).lower()}")
    print(f"hermes_home={result.hermes_home}")
    print(f"workspace={result.workspace}")
    if result.failure:
        print(f"failure={result.failure}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    managed_tmp: tempfile.TemporaryDirectory[str] | None = None

    if args.hermes_home:
        hermes_home = Path(args.hermes_home).expanduser().resolve()
        hermes_home.mkdir(parents=True, exist_ok=True)
    else:
        managed_tmp = tempfile.TemporaryDirectory(prefix="hermes-ava-smoke-")
        hermes_home = Path(managed_tmp.name).resolve()

    if args.workspace:
        workspace = Path(args.workspace).expanduser().resolve()
        workspace.mkdir(parents=True, exist_ok=True)
    else:
        workspace = hermes_home.parent / f"{hermes_home.name}-workspace"
        workspace.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["HERMES_HOME"] = str(hermes_home)
    env["AVA_ENTITY"] = "avaeon-codex"
    env["AVA_WORKSPACE"] = str(workspace)

    codeword = f"AVA-{secrets.token_hex(8).upper()}"
    usage_one = hermes_home / "smoke-usage-1.json"
    usage_two = hermes_home / "smoke-usage-2.json"
    command = _base_command(args)

    first_command = [
        *command,
        "--usage-file",
        str(usage_one),
        "-z",
        f"Remember the exact codeword {codeword}. Reply with exactly STORED.",
    ]
    first = _run(first_command, env=env, cwd=workspace, timeout=args.timeout)

    failure: str | None = None
    first_id: str | None = None
    second_id: str | None = None
    context_restored = False
    stable_session_id = False
    count_before = _session_count(hermes_home / "state.db")
    count_after: int | None = None

    if first.returncode != 0:
        failure = f"first invocation failed ({first.returncode}): {first.stderr.strip()}"
    else:
        try:
            first_usage = _read_usage(usage_one)
            first_id = str(first_usage.get("session_id") or "").strip() or None
        except RuntimeError as exc:
            failure = str(exc)

    if failure is None and not first_id:
        failure = "first invocation did not report a durable session_id"

    if failure is None and first_id:
        second_command = [
            *command,
            "--resume",
            first_id,
            "--usage-file",
            str(usage_two),
            "-z",
            "Return only the exact codeword I asked you to remember in this session.",
        ]
        second = _run(second_command, env=env, cwd=workspace, timeout=args.timeout)
        count_after = _session_count(hermes_home / "state.db")
        if second.returncode != 0:
            failure = f"resume invocation failed ({second.returncode}): {second.stderr.strip()}"
        else:
            try:
                second_usage = _read_usage(usage_two)
                second_id = str(second_usage.get("session_id") or "").strip() or None
            except RuntimeError as exc:
                failure = str(exc)
            context_restored = second.stdout.strip() == codeword
            stable_session_id = second_id == first_id
            if not context_restored and failure is None:
                failure = "resumed invocation did not recover the exact prior codeword"
            if not stable_session_id and failure is None:
                failure = f"session identity changed from {first_id!r} to {second_id!r}"

    no_session_fork: bool | None = None
    if count_before is not None and count_after is not None:
        no_session_fork = count_after == count_before
        if not no_session_fork and failure is None:
            failure = (
                "durable session count changed during resume "
                f"({count_before} -> {count_after})"
            )

    result = SmokeResult(
        status="pass" if failure is None else "fail",
        session_id=first_id,
        first_session_id=first_id,
        second_session_id=second_id,
        context_restored=context_restored,
        stable_session_id=stable_session_id,
        session_count_before_resume=count_before,
        session_count_after_resume=count_after,
        no_session_fork=no_session_fork,
        hermes_home=str(hermes_home),
        workspace=str(workspace),
        failure=failure,
    )
    _emit(result, json_output=args.json_output)

    if managed_tmp is not None and args.keep_temporary_home:
        managed_tmp.cleanup = lambda: None  # type: ignore[method-assign]
        print(f"temporary HERMES_HOME retained at {hermes_home}", file=sys.stderr)
    elif managed_tmp is not None:
        managed_tmp.cleanup()

    return 0 if failure is None else 1


if __name__ == "__main__":
    sys.exit(main())
