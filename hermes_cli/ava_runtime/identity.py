"""Entity and filesystem identity for managed AVA runtimes."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from hermes_cli.ava_runtime.workspace import activate_process_workspace


RUNTIME_ENTITY_ALIASES = {
    "ava": {"ava"},
    "aeon": {"aeon"},
}
RUNTIME_ENTITIES = frozenset(RUNTIME_ENTITY_ALIASES)
OPERATOR_IDENTITIES = frozenset({"avaeon-codex"})
HOST_IDENTITIES = frozenset({"avaorus", "minisforum"})
_INSTANCE_RE = re.compile(r"^(?:live|shadow-[a-z0-9][a-z0-9-]*|test-[a-z0-9][a-z0-9-]*)$")


def validate_operator_identity(value: str) -> str:
    if value.strip().lower() not in OPERATOR_IDENTITIES:
        raise ValueError("operator_id must be exactly avaeon-codex")
    return "avaeon-codex"


def validate_host_identity(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in HOST_IDENTITIES:
        raise ValueError("host_id must be avaorus or minisforum")
    return normalized


def validate_instance_identity(value: str) -> str:
    normalized = value.strip().lower()
    if not _INSTANCE_RE.fullmatch(normalized):
        raise ValueError("instance_id must be live, shadow-*, or test-*")
    return normalized


def _normalized_parts(path: Path) -> set[str]:
    return {part.lower().replace("_", "-") for part in path.parts}


def _is_entity_scoped(path: Path, entity: str) -> bool:
    aliases = {alias.lower().replace("_", "-") for alias in RUNTIME_ENTITY_ALIASES[entity]}
    return bool(_normalized_parts(path) & aliases)


@dataclass(frozen=True)
class ManagedIdentity:
    entity: str
    hermes_home: Path
    workspace: Path

    @classmethod
    def from_env(cls) -> "ManagedIdentity":
        entity = os.environ.get("AVA_ENTITY", "").strip().lower()
        if entity not in RUNTIME_ENTITY_ALIASES:
            raise RuntimeError(
                "AVA_ENTITY must be a runtime entity: " + ", ".join(sorted(RUNTIME_ENTITIES))
            )

        home_raw = os.environ.get("HERMES_HOME", "").strip()
        if not home_raw:
            raise RuntimeError("HERMES_HOME must be exported explicitly.")
        workspace_raw = os.environ.get("AVA_WORKSPACE", "").strip()
        if not workspace_raw:
            raise RuntimeError("AVA_WORKSPACE must be exported explicitly.")

        hermes_home = Path(home_raw).expanduser()
        workspace = Path(workspace_raw).expanduser()
        if not hermes_home.is_absolute() or not workspace.is_absolute():
            raise RuntimeError("HERMES_HOME and AVA_WORKSPACE must be absolute paths.")

        hermes_home = hermes_home.resolve()
        workspace = workspace.resolve()
        for name, path in (("HERMES_HOME", hermes_home), ("AVA_WORKSPACE", workspace)):
            if not path.is_dir():
                raise RuntimeError(f"{name} directory does not exist: {path}")
            if not os.access(path, os.W_OK | os.X_OK):
                raise RuntimeError(f"{name} directory is not writable/searchable: {path}")

        if not _is_entity_scoped(hermes_home, entity):
            raise RuntimeError(
                f"HERMES_HOME {hermes_home} is not visibly scoped to entity {entity}."
            )

        try:
            workspace.relative_to(hermes_home)
        except ValueError:
            pass
        else:
            raise RuntimeError("AVA_WORKSPACE must not live inside HERMES_HOME.")

        return cls(entity=entity, hermes_home=hermes_home, workspace=workspace)

    def activate_workspace(self) -> None:
        activate_process_workspace(
            self.workspace,
            missing_message=f"AVA_WORKSPACE directory does not exist: {self.workspace}",
            enter_message=f"Cannot enter AVA_WORKSPACE: {self.workspace}",
        )
