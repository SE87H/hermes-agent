#!/usr/bin/env python3
"""Control-plane CLI for the managed AVA Hermes fleet.

This command never stores credentials. It validates the non-secret fleet map,
materializes one entity's environment, and invokes the existing doctor, smoke,
or managed one-shot entry points without a shell.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from hermes_cli.ava_runtime.fleet_config import (
    FleetConfig,
    FleetConfigError,
    REQUIRED_ENTITIES,
    load_fleet_config,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "ava-runtime" / "entities.yaml"


def _merged_environment(config: FleetConfig, entity: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(config.environment(entity))
    return env


def _run_child(command: list[str], *, env: dict[str, str], cwd: Path | None) -> int:
    try:
        result = subprocess.run(command, env=env, cwd=cwd, check=False)
    except OSError as exc:
        print(f"cannot execute managed command: {exc}", file=sys.stderr)
        return 127
    return int(result.returncode)


def _doctor_command(config: FleetConfig, entity_name: str, *, expected_ref: str | None, require_state_db: bool, json_output: bool) -> list[str]:
    entity = config.entity(entity_name)
    command = [sys.executable, str(ROOT / "scripts" / "ava_runtime" / "doctor.py"), "--entity", entity.name, "--repo", str(config.source.repository), "--workspace", str(entity.workspace), "--hermes-home", str(entity.hermes_home), "--expected-ref", expected_ref or config.source.expected_ref]
    if config.source.require_clean_checkout:
        command.append("--require-clean")
    if require_state_db:
        command.append("--require-state-db")
    if json_output:
        command.append("--json")
    return command


def _smoke_command(args: argparse.Namespace, config: FleetConfig) -> list[str]:
    entity = config.entity(args.entity)
    command = [sys.executable, str(ROOT / "scripts" / "ava_runtime" / "smoke_session_identity.py"), "--entity", entity.name, "--workspace", str(entity.workspace), "--mode", args.mode, "--timeout", str(args.timeout)]
    if args.live_state:
        command.extend(["--hermes-home", str(entity.hermes_home)])
    if args.model:
        command.extend(["--model", args.model])
    if args.provider:
        command.extend(["--provider", args.provider])
    if args.toolsets:
        command.extend(["--toolsets", args.toolsets])
    if args.keep_temporary_home:
        command.append("--keep-temporary-home")
    if args.json_output:
        command.append("--json")
    return command


def _normalize_remainder(values: Iterable[str]) -> list[str]:
    result = list(values)
    if result and result[0] == "--":
        result = result[1:]
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("AVA_FLEET_CONFIG", "").strip() or str(DEFAULT_CONFIG), help="Non-secret fleet YAML. Defaults to AVA_FLEET_CONFIG or config/ava-runtime/entities.yaml.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate", help="Validate the complete fleet map")
    validate.add_argument("--json", action="store_true", dest="json_output")
    env_parser = subparsers.add_parser("env", help="Render one entity's non-secret environment")
    env_parser.add_argument("entity", choices=sorted(REQUIRED_ENTITIES))
    env_parser.add_argument("--format", choices=("shell", "json"), default="shell")
    doctor = subparsers.add_parser("doctor", help="Run the preflight doctor from fleet configuration")
    doctor.add_argument("entity", choices=[*sorted(REQUIRED_ENTITIES), "all"])
    doctor.add_argument("--expected-ref")
    doctor.add_argument("--require-state-db", action="store_true")
    doctor.add_argument("--json", action="store_true", dest="json_output")
    smoke = subparsers.add_parser("smoke", help="Run the two-turn durable identity smoke")
    smoke.add_argument("entity", choices=sorted(REQUIRED_ENTITIES))
    smoke.add_argument("--mode", choices=("managed", "upstream"), default="managed")
    smoke.add_argument("--live-state", action="store_true")
    smoke.add_argument("--model")
    smoke.add_argument("--provider")
    smoke.add_argument("--toolsets")
    smoke.add_argument("--timeout", type=int, default=300)
    smoke.add_argument("--keep-temporary-home", action="store_true")
    smoke.add_argument("--json", action="store_true", dest="json_output")
    snapshot = subparsers.add_parser("snapshot", help="Create verified SQLite state snapshots")
    snapshot.add_argument("entity", choices=[*sorted(REQUIRED_ENTITIES), "all"])
    oneshot = subparsers.add_parser("oneshot", help="Launch the fail-closed managed one-shot surface")
    oneshot.add_argument("entity", choices=sorted(REQUIRED_ENTITIES))
    oneshot.add_argument("managed_args", nargs=argparse.REMAINDER)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_fleet_config(args.config)
    except FleetConfigError as exc:
        print(f"AVA fleet configuration rejected: {exc}", file=sys.stderr)
        return 2
    if args.command == "validate":
        if args.json_output:
            print(config.summary_json())
        else:
            print("STATUS_CLOSURE=PASS")
            print(f"config_sha256={config.raw_sha256}")
            print("entities=" + ",".join(sorted(config.entities)))
        return 0
    if args.command == "env":
        print(json.dumps(config.environment(args.entity), indent=2, sort_keys=True) if args.format == "json" else config.render_shell_environment(args.entity))
        return 0
    if args.command == "doctor":
        names = sorted(config.entities) if args.entity == "all" else [args.entity]
        exit_code = 0
        for name in names:
            command = _doctor_command(config, name, expected_ref=args.expected_ref, require_state_db=args.require_state_db, json_output=args.json_output)
            exit_code = max(exit_code, _run_child(command, env=_merged_environment(config, name), cwd=config.source.repository if config.source.repository.is_dir() else None))
        return exit_code
    if args.command == "smoke":
        return _run_child(_smoke_command(args, config), env=_merged_environment(config, args.entity), cwd=config.source.repository if config.source.repository.is_dir() else None)
    if args.command == "snapshot":
        names = sorted(config.entities) if args.entity == "all" else [args.entity]
        exit_code = 0
        for name in names:
            entity = config.entity(name)
            command = [sys.executable, str(ROOT / "scripts" / "ava_runtime" / "state_snapshot.py"), "create", "--entity", entity.name, "--hermes-home", str(entity.hermes_home), "--output-root", str(config.snapshot_root)]
            exit_code = max(exit_code, _run_child(command, env=_merged_environment(config, name), cwd=config.source.repository if config.source.repository.is_dir() else None))
        return exit_code
    if args.command == "oneshot":
        managed_args = _normalize_remainder(args.managed_args)
        if not managed_args:
            print("oneshot requires managed arguments and a final prompt", file=sys.stderr)
            return 2
        entity = config.entity(args.entity)
        return _run_child([sys.executable, "-m", "hermes_cli.ava_runtime.managed_oneshot", *managed_args], env=_merged_environment(config, args.entity), cwd=entity.workspace)
    raise AssertionError(f"unhandled fleet command: {args.command}")


if __name__ == "__main__":
    sys.exit(main())
