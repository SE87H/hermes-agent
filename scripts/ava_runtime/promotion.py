#!/usr/bin/env python3
"""Create or verify a promotion manifest for the managed AVA Hermes fleet.

A manifest is generated only after the candidate revision, rollback target,
configuration, test evidence, and per-entity shadow validation all close. The
manifest contains hashes and public status metadata only; never include logs,
transcripts, credentials, or private prompts in the validation report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from hermes_cli.ava_runtime.fleet_config import FleetConfig, FleetConfigError, REQUIRED_ENTITIES, load_fleet_config

SCHEMA_VERSION = 1
REQUIRED_TESTS = frozenset({"ava_runtime", "hermes_cli", "upstream_relevant"})
REQUIRED_ENTITY_GATES = frozenset({"doctor", "identity_smoke", "shadow_runtime"})


class PromotionError(ValueError):
    """Raised when promotion evidence does not close."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PromotionError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _load_json(path: Path, *, label: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PromotionError(f"cannot read {label} {path}: {exc}") from exc
    if not isinstance(value, Mapping):
        raise PromotionError(f"{label} must be a JSON object: {path}")
    return value


def _status(value: object, *, field: str) -> str:
    if isinstance(value, str):
        result = value.strip().lower()
    elif isinstance(value, Mapping):
        raw = value.get("status")
        result = raw.strip().lower() if isinstance(raw, str) else ""
    else:
        result = ""
    if result != "pass":
        raise PromotionError(f"{field} must have status 'pass', found {result or value!r}")
    return result


def _require_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PromotionError(f"{field} must be a non-empty string")
    return value.strip()


def _validate_report(report: Mapping[str, Any]) -> dict[str, Any]:
    if report.get("schema_version") != SCHEMA_VERSION:
        raise PromotionError(f"unsupported validation report schema {report.get('schema_version')!r}; expected {SCHEMA_VERSION}")
    _status(report.get("status_closure"), field="status_closure")
    candidate_commit = _require_text(report.get("candidate_commit"), field="candidate_commit")
    rollback_ref = _require_text(report.get("rollback_ref"), field="rollback_ref")
    tests_raw = report.get("tests")
    if not isinstance(tests_raw, Mapping):
        raise PromotionError("tests must be a JSON object")
    missing_tests = REQUIRED_TESTS - set(tests_raw)
    if missing_tests:
        raise PromotionError("missing required test gates: " + ", ".join(sorted(missing_tests)))
    tests: dict[str, Any] = {}
    for name in sorted(REQUIRED_TESTS):
        _status(tests_raw[name], field=f"tests.{name}")
        tests[name] = tests_raw[name]
    entities_raw = report.get("entities")
    if not isinstance(entities_raw, Mapping):
        raise PromotionError("entities must be a JSON object")
    if set(entities_raw) != set(REQUIRED_ENTITIES):
        raise PromotionError("validation report must contain exactly: " + ", ".join(sorted(REQUIRED_ENTITIES)))
    entities: dict[str, Any] = {}
    for entity_name in sorted(REQUIRED_ENTITIES):
        entity_raw = entities_raw[entity_name]
        if not isinstance(entity_raw, Mapping):
            raise PromotionError(f"entities.{entity_name} must be a JSON object")
        missing_gates = REQUIRED_ENTITY_GATES - set(entity_raw)
        if missing_gates:
            raise PromotionError(f"entities.{entity_name} missing gates: " + ", ".join(sorted(missing_gates)))
        for gate in sorted(REQUIRED_ENTITY_GATES):
            _status(entity_raw[gate], field=f"entities.{entity_name}.{gate}")
        entities[entity_name] = dict(entity_raw)
    remaining_gaps = report.get("remaining_gaps", [])
    if remaining_gaps not in (None, []):
        raise PromotionError("remaining_gaps must be empty before promotion")
    return {"candidate_commit": candidate_commit, "rollback_ref": rollback_ref, "tests": tests, "entities": entities}


def _git(repo: Path, *args: str) -> str:
    try:
        result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PromotionError(f"git {' '.join(args)} failed: {exc}") from exc
    if result.returncode != 0:
        raise PromotionError(f"git {' '.join(args)} failed: {(result.stderr or result.stdout).strip()}")
    return result.stdout.strip()


def _git_state(repo: Path, rollback_ref: str) -> dict[str, str]:
    head = _git(repo, "rev-parse", "HEAD")
    tree = _git(repo, "rev-parse", "HEAD^{tree}")
    rollback_commit = _git(repo, "rev-parse", "--verify", f"{rollback_ref}^{{commit}}")
    status = _git(repo, "status", "--porcelain=v1", "--untracked-files=normal")
    if status:
        raise PromotionError("working tree must be clean before promotion")
    if rollback_commit == head:
        raise PromotionError("rollback target must differ from the candidate commit")
    return {"head": head, "tree": tree, "rollback_commit": rollback_commit}


def _atomic_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def build_manifest(*, config: FleetConfig, report_path: Path, report: Mapping[str, Any], repo: Path) -> dict[str, Any]:
    validated = _validate_report(report)
    git_state = _git_state(repo, validated["rollback_ref"])
    if validated["candidate_commit"] != git_state["head"]:
        raise PromotionError("validation report candidate_commit does not match repository HEAD: " f"report={validated['candidate_commit']}, head={git_state['head']}")
    return {
        "schema_version": SCHEMA_VERSION,
        "status_closure": "pass",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": {"repository": str(repo.resolve()), "candidate_commit": git_state["head"], "candidate_tree": git_state["tree"], "rollback_ref": validated["rollback_ref"], "rollback_commit": git_state["rollback_commit"]},
        "evidence": {"fleet_config": str(config.path), "fleet_config_sha256": config.raw_sha256, "validation_report": str(report_path.resolve()), "validation_report_sha256": _sha256(report_path), "tests": validated["tests"], "entities": validated["entities"]},
    }


def verify_manifest(*, manifest: Mapping[str, Any], config: FleetConfig, report_path: Path, report: Mapping[str, Any], repo: Path) -> None:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise PromotionError("unsupported promotion manifest schema")
    _status(manifest.get("status_closure"), field="manifest.status_closure")
    validated = _validate_report(report)
    source = manifest.get("source")
    evidence = manifest.get("evidence")
    if not isinstance(source, Mapping) or not isinstance(evidence, Mapping):
        raise PromotionError("manifest source/evidence blocks are required")
    git_state = _git_state(repo, validated["rollback_ref"])
    expected_pairs = {
        "source.repository": (source.get("repository"), str(repo.resolve())),
        "source.candidate_commit": (source.get("candidate_commit"), git_state["head"]),
        "source.candidate_tree": (source.get("candidate_tree"), git_state["tree"]),
        "source.rollback_ref": (source.get("rollback_ref"), validated["rollback_ref"]),
        "source.rollback_commit": (source.get("rollback_commit"), git_state["rollback_commit"]),
        "evidence.fleet_config_sha256": (evidence.get("fleet_config_sha256"), config.raw_sha256),
        "evidence.validation_report_sha256": (evidence.get("validation_report_sha256"), _sha256(report_path)),
    }
    for field, (actual, expected) in expected_pairs.items():
        if actual != expected:
            raise PromotionError(f"manifest drift at {field}: {actual!r} != {expected!r}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("prepare", "verify"):
        command = subparsers.add_parser(name)
        command.add_argument("--config", required=True)
        command.add_argument("--report", required=True)
        command.add_argument("--repo")
        command.add_argument("--manifest", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_fleet_config(args.config)
        repo = Path(args.repo).expanduser().resolve() if args.repo else config.source.repository
        report_path = Path(args.report).expanduser().resolve()
        report = _load_json(report_path, label="validation report")
        manifest_path = Path(args.manifest).expanduser().resolve()
        if args.command == "prepare":
            _atomic_write_json(manifest_path, build_manifest(config=config, report_path=report_path, report=report, repo=repo))
        else:
            verify_manifest(manifest=_load_json(manifest_path, label="promotion manifest"), config=config, report_path=report_path, report=report, repo=repo)
        print("STATUS_CLOSURE=PASS")
        print(f"candidate_commit={_git(repo, 'rev-parse', 'HEAD')}")
        print(f"manifest={manifest_path}")
        return 0
    except (FleetConfigError, PromotionError) as exc:
        print(f"promotion rejected: {exc}", file=sys.stderr)
        print("STATUS_CLOSURE=FAIL", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
