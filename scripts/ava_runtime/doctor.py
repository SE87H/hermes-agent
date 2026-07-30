#!/usr/bin/env python3
"""Preflight checks for a managed AVA Hermes runtime.

This script reads paths and Git metadata only. It never reads or prints provider
credentials, prompt contents, memories, or session transcripts.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


ENTITY_ALIASES = {
    "ava": {"ava"},
    "aeon": {"aeon"},
    "avaeon-codex": {"avaeon-codex", "avaeon_codex", "avaeoncodex"},
}


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    message: str


class Doctor:
    def __init__(self) -> None:
        self.checks: list[Check] = []

    def ok(self, name: str, message: str) -> None:
        self.checks.append(Check(name, "ok", message))

    def warn(self, name: str, message: str) -> None:
        self.checks.append(Check(name, "warning", message))

    def fail(self, name: str, message: str) -> None:
        self.checks.append(Check(name, "error", message))

    @property
    def failed(self) -> bool:
        return any(check.status == "error" for check in self.checks)


def _run_git(repo: Path, *args: str) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return result.returncode, result.stdout.strip(), result.stderr.strip()


def _path_contains_entity(path: Path, entity: str) -> bool:
    normalized_parts = {part.lower().replace("_", "-") for part in path.parts}
    aliases = {alias.lower().replace("_", "-") for alias in ENTITY_ALIASES[entity]}
    return bool(normalized_parts & aliases)


def _check_directory(
    doctor: Doctor,
    *,
    name: str,
    path: Path,
    require_exists: bool,
    require_writable: bool,
) -> None:
    if not path.is_absolute():
        doctor.fail(name, f"path must be absolute: {path}")
        return
    if not path.exists():
        if require_exists:
            doctor.fail(name, f"directory does not exist: {path}")
        else:
            doctor.warn(name, f"directory does not exist yet: {path}")
        return
    if not path.is_dir():
        doctor.fail(name, f"path is not a directory: {path}")
        return
    if require_writable and not os.access(path, os.W_OK | os.X_OK):
        doctor.fail(name, f"directory is not writable/searchable: {path}")
        return
    doctor.ok(name, str(path))


def _check_git(
    doctor: Doctor,
    repo: Path,
    expected_ref: str | None,
    require_clean: bool,
) -> None:
    rc, head, error = _run_git(repo, "rev-parse", "HEAD")
    if rc != 0:
        doctor.fail("git.repository", error or f"not a Git repository: {repo}")
        return
    doctor.ok("git.head", head)

    rc, branch, _ = _run_git(repo, "branch", "--show-current")
    if rc == 0:
        doctor.ok("git.branch", branch or "detached HEAD")

    if expected_ref:
        rc, expected_commit, error = _run_git(repo, "rev-parse", "--verify", f"{expected_ref}^{{commit}}")
        if rc != 0:
            doctor.fail("git.expected_ref", error or f"cannot resolve {expected_ref!r}")
        elif expected_commit != head:
            doctor.fail(
                "git.expected_ref",
                f"HEAD {head} does not match approved ref {expected_ref} ({expected_commit})",
            )
        else:
            doctor.ok("git.expected_ref", f"HEAD matches {expected_ref}")

    rc, status, error = _run_git(repo, "status", "--porcelain=v1", "--untracked-files=normal")
    if rc != 0:
        doctor.fail("git.clean", error or "cannot inspect working tree")
    elif status:
        message = "working tree contains local changes"
        if require_clean:
            doctor.fail("git.clean", message)
        else:
            doctor.warn("git.clean", message)
    else:
        doctor.ok("git.clean", "working tree is clean")


def _resolve_path(cli_value: str | None, env_name: str) -> Path | None:
    raw = (cli_value or os.environ.get(env_name, "")).strip()
    return Path(raw).expanduser() if raw else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--entity",
        choices=sorted(ENTITY_ALIASES),
        default=os.environ.get("AVA_ENTITY", "").strip().lower() or None,
        help="Runtime identity. Defaults to AVA_ENTITY.",
    )
    parser.add_argument("--repo", help="Hermes source checkout. Defaults to AVA_HERMES_REPO.")
    parser.add_argument("--workspace", help="Entity workspace. Defaults to AVA_WORKSPACE.")
    parser.add_argument("--hermes-home", help="Hermes state root. Defaults to HERMES_HOME.")
    parser.add_argument(
        "--expected-ref",
        default=os.environ.get("AVA_HERMES_EXPECTED_REF", "").strip() or None,
        help="Approved branch, tag, or commit that HEAD must match.",
    )
    parser.add_argument("--require-clean", action="store_true")
    parser.add_argument("--require-state-db", action="store_true")
    parser.add_argument(
        "--allow-unscoped-home",
        action="store_true",
        help="Permit a HERMES_HOME path that does not contain the entity name.",
    )
    parser.add_argument("--json", action="store_true", dest="json_output")
    return parser


def _render_text(checks: Iterable[Check]) -> None:
    glyph = {"ok": "PASS", "warning": "WARN", "error": "FAIL"}
    for check in checks:
        print(f"[{glyph[check.status]}] {check.name}: {check.message}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    doctor = Doctor()

    if not args.entity:
        doctor.fail("identity.entity", "set --entity or AVA_ENTITY")
        entity = None
    else:
        entity = args.entity
        doctor.ok("identity.entity", entity)

    repo = _resolve_path(args.repo, "AVA_HERMES_REPO")
    workspace = _resolve_path(args.workspace, "AVA_WORKSPACE")
    hermes_home = _resolve_path(args.hermes_home, "HERMES_HOME")

    if repo is None:
        doctor.fail("path.repo", "set --repo or AVA_HERMES_REPO")
    else:
        _check_directory(
            doctor,
            name="path.repo",
            path=repo,
            require_exists=True,
            require_writable=False,
        )
        if repo.is_dir():
            _check_git(doctor, repo, args.expected_ref, args.require_clean)

    if workspace is None:
        doctor.fail("path.workspace", "set --workspace or AVA_WORKSPACE")
    else:
        _check_directory(
            doctor,
            name="path.workspace",
            path=workspace,
            require_exists=True,
            require_writable=True,
        )

    if hermes_home is None:
        doctor.fail("path.hermes_home", "HERMES_HOME must be exported explicitly")
    else:
        _check_directory(
            doctor,
            name="path.hermes_home",
            path=hermes_home,
            require_exists=True,
            require_writable=True,
        )
        if entity and not args.allow_unscoped_home:
            if _path_contains_entity(hermes_home, entity):
                doctor.ok("isolation.hermes_home", "path is scoped to the selected entity")
            else:
                doctor.fail(
                    "isolation.hermes_home",
                    f"{hermes_home} does not contain an entity scope for {entity}",
                )
        state_db = hermes_home / "state.db"
        if state_db.is_file():
            doctor.ok("state.database", str(state_db))
        elif args.require_state_db:
            doctor.fail("state.database", f"missing required database: {state_db}")
        else:
            doctor.warn("state.database", f"not created yet: {state_db}")

    if workspace and hermes_home:
        try:
            workspace.resolve().relative_to(hermes_home.resolve())
        except ValueError:
            doctor.ok("isolation.workspace", "workspace is outside HERMES_HOME")
        else:
            doctor.fail("isolation.workspace", "workspace must not live inside HERMES_HOME")

    payload = {
        "status": "fail" if doctor.failed else "pass",
        "entity": entity,
        "checks": [asdict(check) for check in doctor.checks],
    }
    if args.json_output:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        _render_text(doctor.checks)
        print(f"STATUS_CLOSURE={payload['status'].upper()}")
    return 1 if doctor.failed else 0


if __name__ == "__main__":
    sys.exit(main())
