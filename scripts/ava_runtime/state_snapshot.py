#!/usr/bin/env python3
"""Create or verify an atomic SQLite state snapshot for one managed entity.

The snapshot contains only ``state.db`` and a checksum manifest. It does not copy
configuration files, credentials, logs, caches, prompts, or other HERMES_HOME
content. The destination must be local protected storage outside HERMES_HOME.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from hermes_cli.ava_runtime.identity import RUNTIME_ENTITY_ALIASES

ENTITY_ALIASES = RUNTIME_ENTITY_ALIASES

SCHEMA_VERSION = 1


class SnapshotError(ValueError):
    """Raised when a snapshot cannot be proven safe or valid."""


def _normalize_entity(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    for entity, aliases in ENTITY_ALIASES.items():
        candidates = {alias.lower().replace("_", "-") for alias in aliases}
        if normalized in candidates:
            return entity
    raise SnapshotError(f"unknown entity {value!r}; expected one of: {', '.join(sorted(ENTITY_ALIASES))}")


def _is_entity_scoped(path: Path, entity: str) -> bool:
    aliases = {alias.lower().replace("_", "-") for alias in ENTITY_ALIASES[entity]}
    parts = {part.lower().replace("_", "-") for part in path.parts}
    return bool(parts & aliases)


def _within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise SnapshotError(f"cannot hash {path}: {exc}") from exc
    return digest.hexdigest()


def _sqlite_metadata(path: Path) -> dict[str, Any]:
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30) as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()
            if not integrity or str(integrity[0]).lower() != "ok":
                raise SnapshotError(f"SQLite integrity_check failed for {path}: {integrity}")
            page_count = int(conn.execute("PRAGMA page_count").fetchone()[0])
            page_size = int(conn.execute("PRAGMA page_size").fetchone()[0])
            user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            schema_version = int(conn.execute("PRAGMA schema_version").fetchone()[0])
    except sqlite3.Error as exc:
        raise SnapshotError(f"cannot inspect SQLite database {path}: {exc}") from exc
    return {"integrity_check": "ok", "page_count": page_count, "page_size": page_size, "user_version": user_version, "schema_version": schema_version}


def _backup_sqlite(source: Path, destination: Path) -> None:
    try:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=30) as source_conn:
            with sqlite3.connect(destination, timeout=30) as destination_conn:
                source_conn.backup(destination_conn)
    except sqlite3.Error as exc:
        raise SnapshotError(f"SQLite backup failed: {exc}") from exc
    os.chmod(destination, 0o600)


def _write_manifest(path: Path, payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(path, 0o600)
    except OSError as exc:
        raise SnapshotError(f"cannot write snapshot manifest {path}: {exc}") from exc


def _load_manifest(path: Path) -> Mapping[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"cannot read snapshot manifest {path}: {exc}") from exc
    if not isinstance(payload, Mapping):
        raise SnapshotError("snapshot manifest must be a JSON object")
    return payload


def create_snapshot(*, entity: str, hermes_home: Path, output_root: Path) -> Path:
    entity = _normalize_entity(entity)
    hermes_home = hermes_home.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    if not hermes_home.is_dir():
        raise SnapshotError(f"HERMES_HOME does not exist: {hermes_home}")
    if not _is_entity_scoped(hermes_home, entity):
        raise SnapshotError(f"HERMES_HOME is not visibly scoped to {entity}: {hermes_home}")
    if _within(output_root, hermes_home):
        raise SnapshotError("snapshot output root must be outside HERMES_HOME")
    source = hermes_home / "state.db"
    if source.is_symlink():
        raise SnapshotError(f"refusing symlinked state database: {source}")
    if not source.is_file():
        raise SnapshotError(f"state database does not exist: {source}")
    output_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(output_root, 0o700)
    temporary = Path(tempfile.mkdtemp(prefix=f".{entity}-snapshot-", dir=output_root))
    os.chmod(temporary, 0o700)
    try:
        snapshot_db = temporary / "state.db"
        _backup_sqlite(source, snapshot_db)
        metadata = _sqlite_metadata(snapshot_db)
        checksum = _sha256(snapshot_db)
        timestamp = datetime.now(timezone.utc)
        _write_manifest(temporary / "manifest.json", {"schema_version": SCHEMA_VERSION, "status_closure": "pass", "entity": entity, "created_at": timestamp.isoformat(), "source_state_db": str(source), "snapshot_file": "state.db", "state_db_sha256": checksum, "sqlite": metadata})
        destination = output_root / f"{entity}-{timestamp.strftime('%Y%m%dT%H%M%SZ')}-{checksum[:12]}"
        if destination.exists():
            raise SnapshotError(f"snapshot destination already exists: {destination}")
        temporary.replace(destination)
        return destination
    except BaseException:
        shutil.rmtree(temporary, ignore_errors=True)
        raise


def verify_snapshot(snapshot_dir: Path) -> Mapping[str, Any]:
    snapshot_dir = snapshot_dir.expanduser().resolve()
    if not snapshot_dir.is_dir():
        raise SnapshotError(f"snapshot directory does not exist: {snapshot_dir}")
    manifest = _load_manifest(snapshot_dir / "manifest.json")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise SnapshotError("unsupported snapshot manifest schema")
    if manifest.get("status_closure") != "pass":
        raise SnapshotError("snapshot manifest is not closed")
    entity = _normalize_entity(str(manifest.get("entity") or ""))
    snapshot_name = manifest.get("snapshot_file")
    if snapshot_name != "state.db":
        raise SnapshotError(f"unexpected snapshot_file: {snapshot_name!r}")
    snapshot_db = snapshot_dir / snapshot_name
    if snapshot_db.is_symlink() or not snapshot_db.is_file():
        raise SnapshotError(f"snapshot database is missing or symlinked: {snapshot_db}")
    actual_hash = _sha256(snapshot_db)
    expected_hash = manifest.get("state_db_sha256")
    if actual_hash != expected_hash:
        raise SnapshotError(f"snapshot checksum mismatch: expected {expected_hash}, found {actual_hash}")
    metadata = _sqlite_metadata(snapshot_db)
    recorded_metadata = manifest.get("sqlite")
    if not isinstance(recorded_metadata, Mapping):
        raise SnapshotError("snapshot manifest lacks SQLite metadata")
    for key in ("page_count", "page_size", "user_version", "schema_version"):
        if recorded_metadata.get(key) != metadata.get(key):
            raise SnapshotError(f"snapshot SQLite metadata drift at {key}: {recorded_metadata.get(key)!r} != {metadata.get(key)!r}")
    return {"entity": entity, "state_db_sha256": actual_hash, "sqlite": metadata}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--entity", required=True)
    create.add_argument("--hermes-home", required=True)
    create.add_argument("--output-root", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("snapshot_dir")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "create":
            snapshot = create_snapshot(entity=args.entity, hermes_home=Path(args.hermes_home), output_root=Path(args.output_root))
            result = verify_snapshot(snapshot)
            print("STATUS_CLOSURE=PASS")
            print(f"snapshot={snapshot}")
        else:
            result = verify_snapshot(Path(args.snapshot_dir))
            print("STATUS_CLOSURE=PASS")
        print(f"entity={result['entity']}")
        print(f"state_db_sha256={result['state_db_sha256']}")
        return 0
    except SnapshotError as exc:
        print(f"snapshot rejected: {exc}", file=sys.stderr)
        print("STATUS_CLOSURE=FAIL", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
