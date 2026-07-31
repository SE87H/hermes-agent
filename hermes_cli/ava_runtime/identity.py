"""Entity and filesystem identity for managed AVA runtimes."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from hermes_cli.ava_runtime.workspace import activate_process_workspace


ENTITY_ALIASES = {
    "ava": {"ava"},
    "aeon": {"aeon"},
    "avaeon-codex": {"avaeon-codex", "avaeon_codex", "avaeoncodex"},
}


def _normalized_parts(path: Path) -> set[str]:
    return {part.lower().replace("_", "-") for part in path.parts}


def _is_entity_scoped(path: Path, entity: str) -> bool:
    aliases = {alias.lower().replace("_", "-") for alias in ENTITY_ALIASES[entity]}
    return bool(_normalized_parts(path) & aliases)


@dataclass(frozen=True)
class ManagedIdentity:
    entity: str
    hermes_home: Path
    workspace: Path

    @classmethod
    def from_env(cls) -> "ManagedIdentity":
        entity = os.environ.get("AVA_ENTITY", "").strip().lower()
        if entity not in ENTITY_ALIASES:
            raise RuntimeError(
                "AVA_ENTITY must be one of: " + ", ".join(sorted(ENTITY_ALIASES))
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
