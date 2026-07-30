"""Validated fleet configuration for AVA managed Hermes runtimes.

The configuration is intentionally non-secret. It binds each synthetic entity to
an explicit Hermes state root, workspace, and reviewed source checkout. Secrets
remain in the corresponding ``HERMES_HOME`` or external secret stores.
"""

from __future__ import annotations

import hashlib
import json
import re
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml

from hermes_cli.ava_runtime.identity import ENTITY_ALIASES


SCHEMA_VERSION = 1
REQUIRED_ENTITIES = frozenset(ENTITY_ALIASES)
_COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_POLICY = {
    "explicit_hermes_home": "required",
    "resume_missing_session": "fail",
    "resume_missing_workspace": "fail",
    "resume_ambiguous_lineage": "fail",
    "restore_recorded_workspace": True,
    "allow_restore_workspace_opt_out": True,
    "global_most_recent_session": "forbidden_for_managed_entities",
    "terminal_backend_failure": "fail",
    "deployment_ref": "pinned_commit_or_stable_tag",
    "secrets_in_repository": "forbidden",
}


class FleetConfigError(ValueError):
    """Raised when the fleet configuration violates an operational invariant."""


def _normalized_entity(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    for entity, aliases in ENTITY_ALIASES.items():
        normalized_aliases = {alias.lower().replace("_", "-") for alias in aliases}
        if normalized in normalized_aliases:
            return entity
    raise FleetConfigError(
        f"unknown entity {value!r}; expected one of: {', '.join(sorted(REQUIRED_ENTITIES))}"
    )


def _absolute_path(raw: object, *, field: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise FleetConfigError(f"{field} must be a non-empty absolute path")
    path = Path(raw).expanduser()
    if not path.is_absolute():
        raise FleetConfigError(f"{field} must be absolute: {path}")
    return path.resolve(strict=False)


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _path_scoped_to_entity(path: Path, entity: str) -> bool:
    aliases = {alias.lower().replace("_", "-") for alias in ENTITY_ALIASES[entity]}
    parts = {part.lower().replace("_", "-") for part in path.parts}
    return bool(parts & aliases)


def _require_mapping(value: object, *, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise FleetConfigError(f"{field} must be a mapping")
    return value


def _require_text(value: object, *, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise FleetConfigError(f"{field} must be a non-empty string")
    return value.strip()


def _require_bool(value: object, *, field: str) -> bool:
    if not isinstance(value, bool):
        raise FleetConfigError(f"{field} must be a boolean")
    return value


def _reject_unknown(mapping: Mapping[str, Any], *, field: str, allowed: set[str]) -> None:
    unknown = set(mapping) - allowed
    if unknown:
        raise FleetConfigError(
            f"{field} contains unknown fields: " + ", ".join(sorted(str(item) for item in unknown))
        )


@dataclass(frozen=True)
class SourceConfig:
    repository: Path
    expected_ref: str
    require_clean_checkout: bool
    auto_update: bool


@dataclass(frozen=True)
class PromotionConfig:
    upstream_snapshot: str
    staging: str
    stable: str
    require_shadow_validation: bool
    require_rollback_ref: bool


@dataclass(frozen=True)
class EntityConfig:
    name: str
    hermes_home: Path
    workspace: Path
    profile: str
    session_scope: str

    def environment(self, source: SourceConfig) -> dict[str, str]:
        return {
            "AVA_ENTITY": self.name,
            "HERMES_HOME": str(self.hermes_home),
            "AVA_WORKSPACE": str(self.workspace),
            "AVA_HERMES_REPO": str(source.repository),
            "AVA_HERMES_EXPECTED_REF": source.expected_ref,
            "AVA_PROFILE": self.profile,
            "AVA_SESSION_SCOPE": self.session_scope,
        }


@dataclass(frozen=True)
class FleetConfig:
    path: Path
    source: SourceConfig
    promotion: PromotionConfig
    policy: Mapping[str, object]
    entities: Mapping[str, EntityConfig]
    snapshot_root: Path
    raw_sha256: str

    def entity(self, name: str) -> EntityConfig:
        canonical = _normalized_entity(name)
        try:
            return self.entities[canonical]
        except KeyError as exc:
            raise FleetConfigError(f"entity {canonical!r} is not configured") from exc

    def environment(self, name: str) -> dict[str, str]:
        return self.entity(name).environment(self.source)

    def render_shell_environment(self, name: str) -> str:
        environment = self.environment(name)
        return "\n".join(
            f"export {key}={shlex.quote(value)}" for key, value in sorted(environment.items())
        )

    def public_summary(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "config_path": str(self.path),
            "config_sha256": self.raw_sha256,
            "source": {
                "repository": str(self.source.repository),
                "expected_ref": self.source.expected_ref,
                "require_clean_checkout": self.source.require_clean_checkout,
                "auto_update": self.source.auto_update,
            },
            "promotion": {
                "upstream_snapshot": self.promotion.upstream_snapshot,
                "staging": self.promotion.staging,
                "stable": self.promotion.stable,
                "require_shadow_validation": self.promotion.require_shadow_validation,
                "require_rollback_ref": self.promotion.require_rollback_ref,
            },
            "policy": dict(self.policy),
            "snapshots": {"root": str(self.snapshot_root)},
            "entities": {
                name: {
                    "hermes_home": str(entity.hermes_home),
                    "workspace": str(entity.workspace),
                    "profile": entity.profile,
                    "session_scope": entity.session_scope,
                }
                for name, entity in sorted(self.entities.items())
            },
        }

    def summary_json(self) -> str:
        return json.dumps(self.public_summary(), indent=2, sort_keys=True)


def _parse_source(raw: Mapping[str, Any]) -> SourceConfig:
    source_raw = _require_mapping(raw.get("source"), field="source")
    _reject_unknown(
        source_raw,
        field="source",
        allowed={"repository", "expected_ref", "require_clean_checkout", "auto_update"},
    )
    expected_ref = _require_text(source_raw.get("expected_ref"), field="source.expected_ref").lower()
    if not _COMMIT_RE.fullmatch(expected_ref):
        raise FleetConfigError(
            "source.expected_ref must be an exact 40-character lowercase commit SHA"
        )
    require_clean = _require_bool(
        source_raw.get("require_clean_checkout"), field="source.require_clean_checkout"
    )
    auto_update = _require_bool(source_raw.get("auto_update"), field="source.auto_update")
    if not require_clean:
        raise FleetConfigError("source.require_clean_checkout cannot be disabled")
    if auto_update:
        raise FleetConfigError("source.auto_update must remain false for managed runtimes")
    return SourceConfig(
        repository=_absolute_path(source_raw.get("repository"), field="source.repository"),
        expected_ref=expected_ref,
        require_clean_checkout=require_clean,
        auto_update=auto_update,
    )


def _parse_promotion(raw: Mapping[str, Any]) -> PromotionConfig:
    promotion_raw = _require_mapping(raw.get("promotion"), field="promotion")
    _reject_unknown(
        promotion_raw,
        field="promotion",
        allowed={
            "upstream_snapshot",
            "staging",
            "stable",
            "require_shadow_validation",
            "require_rollback_ref",
        },
    )
    require_shadow = _require_bool(
        promotion_raw.get("require_shadow_validation"),
        field="promotion.require_shadow_validation",
    )
    require_rollback = _require_bool(
        promotion_raw.get("require_rollback_ref"),
        field="promotion.require_rollback_ref",
    )
    if not require_shadow or not require_rollback:
        raise FleetConfigError("promotion shadow validation and rollback gates cannot be disabled")
    return PromotionConfig(
        upstream_snapshot=_require_text(
            promotion_raw.get("upstream_snapshot"), field="promotion.upstream_snapshot"
        ),
        staging=_require_text(promotion_raw.get("staging"), field="promotion.staging"),
        stable=_require_text(promotion_raw.get("stable"), field="promotion.stable"),
        require_shadow_validation=require_shadow,
        require_rollback_ref=require_rollback,
    )


def _parse_policy(raw: Mapping[str, Any]) -> dict[str, object]:
    policy_raw = _require_mapping(raw.get("policy"), field="policy")
    _reject_unknown(policy_raw, field="policy", allowed=set(REQUIRED_POLICY))
    missing = set(REQUIRED_POLICY) - set(policy_raw)
    if missing:
        raise FleetConfigError("policy is missing fields: " + ", ".join(sorted(missing)))
    for name, expected in REQUIRED_POLICY.items():
        if policy_raw.get(name) != expected:
            raise FleetConfigError(
                f"policy.{name} cannot be lowered: expected {expected!r}, "
                f"found {policy_raw.get(name)!r}"
            )
    return dict(policy_raw)


def _parse_snapshot_root(raw: Mapping[str, Any]) -> Path:
    snapshots_raw = _require_mapping(raw.get("snapshots"), field="snapshots")
    _reject_unknown(snapshots_raw, field="snapshots", allowed={"root"})
    return _absolute_path(snapshots_raw.get("root"), field="snapshots.root")


def _parse_entities(raw: Mapping[str, Any]) -> dict[str, EntityConfig]:
    entities_raw = _require_mapping(raw.get("entities"), field="entities")
    canonical_keys: dict[str, str] = {}
    for supplied_name in entities_raw:
        if not isinstance(supplied_name, str):
            raise FleetConfigError("entity names must be strings")
        canonical = _normalized_entity(supplied_name)
        if canonical in canonical_keys:
            raise FleetConfigError(
                f"duplicate aliases for entity {canonical!r}: "
                f"{canonical_keys[canonical]!r} and {supplied_name!r}"
            )
        canonical_keys[canonical] = supplied_name

    missing = REQUIRED_ENTITIES - set(canonical_keys)
    extra = set(canonical_keys) - REQUIRED_ENTITIES
    if missing or extra:
        details = []
        if missing:
            details.append("missing=" + ",".join(sorted(missing)))
        if extra:
            details.append("extra=" + ",".join(sorted(extra)))
        raise FleetConfigError(
            "entities must define the complete managed fleet (" + "; ".join(details) + ")"
        )

    entities: dict[str, EntityConfig] = {}
    for canonical, supplied_name in canonical_keys.items():
        entity_raw = _require_mapping(entities_raw[supplied_name], field=f"entities.{supplied_name}")
        _reject_unknown(
            entity_raw,
            field=f"entities.{supplied_name}",
            allowed={"hermes_home", "workspace", "profile", "session_scope"},
        )
        hermes_home = _absolute_path(
            entity_raw.get("hermes_home"), field=f"entities.{supplied_name}.hermes_home"
        )
        workspace = _absolute_path(
            entity_raw.get("workspace"), field=f"entities.{supplied_name}.workspace"
        )
        profile = _require_text(entity_raw.get("profile"), field=f"entities.{supplied_name}.profile")
        session_scope = _require_text(
            entity_raw.get("session_scope"), field=f"entities.{supplied_name}.session_scope"
        )
        if not _path_scoped_to_entity(hermes_home, canonical):
            raise FleetConfigError(
                f"entities.{supplied_name}.hermes_home is not visibly scoped to {canonical}: {hermes_home}"
            )
        if _is_within(workspace, hermes_home):
            raise FleetConfigError(
                f"entities.{supplied_name}.workspace must not live inside its HERMES_HOME"
            )
        entities[canonical] = EntityConfig(
            name=canonical,
            hermes_home=hermes_home,
            workspace=workspace,
            profile=profile,
            session_scope=session_scope,
        )
    return entities


def _validate_cross_entity_isolation(
    source: SourceConfig, entities: Mapping[str, EntityConfig], snapshot_root: Path
) -> None:
    homes: dict[Path, str] = {}
    workspaces: dict[Path, str] = {}
    profiles: dict[str, str] = {}
    scopes: dict[str, str] = {}

    for name, entity in entities.items():
        if entity.hermes_home in homes:
            raise FleetConfigError(
                f"HERMES_HOME collision: {name} and {homes[entity.hermes_home]} use {entity.hermes_home}"
            )
        homes[entity.hermes_home] = name
        if entity.workspace in workspaces:
            raise FleetConfigError(
                f"workspace collision: {name} and {workspaces[entity.workspace]} use {entity.workspace}"
            )
        workspaces[entity.workspace] = name
        profile_key = entity.profile.casefold()
        if profile_key in profiles:
            raise FleetConfigError(
                f"profile collision: {name} and {profiles[profile_key]} use {entity.profile!r}"
            )
        profiles[profile_key] = name
        scope_key = entity.session_scope.casefold()
        if scope_key in scopes:
            raise FleetConfigError(
                f"session_scope collision: {name} and {scopes[scope_key]} use {entity.session_scope!r}"
            )
        scopes[scope_key] = name

    ordered = list(entities.values())
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if _is_within(left.hermes_home, right.hermes_home) or _is_within(
                right.hermes_home, left.hermes_home
            ):
                raise FleetConfigError(
                    f"nested HERMES_HOME roots are forbidden: {left.name}={left.hermes_home}, "
                    f"{right.name}={right.hermes_home}"
                )
            if _is_within(left.workspace, right.workspace) or _is_within(
                right.workspace, left.workspace
            ):
                raise FleetConfigError(
                    f"nested workspaces are forbidden: {left.name}={left.workspace}, "
                    f"{right.name}={right.workspace}"
                )

    for workspace, workspace_owner in workspaces.items():
        for home, home_owner in homes.items():
            if _is_within(workspace, home):
                raise FleetConfigError(
                    f"{workspace_owner}'s workspace {workspace} is inside "
                    f"{home_owner}'s HERMES_HOME {home}"
                )

    protected_paths: list[tuple[str, Path]] = [
        ("source.repository", source.repository),
        ("snapshots.root", snapshot_root),
    ]
    protected_paths.extend((f"entities.{owner}.hermes_home", path) for path, owner in homes.items())
    protected_paths.extend((f"entities.{owner}.workspace", path) for path, owner in workspaces.items())
    for index, (left_name, left_path) in enumerate(protected_paths):
        for right_name, right_path in protected_paths[index + 1 :]:
            if _is_within(left_path, right_path) or _is_within(right_path, left_path):
                raise FleetConfigError(
                    f"managed roots must be disjoint: {left_name}={left_path}, "
                    f"{right_name}={right_path}"
                )


def load_fleet_config(path: str | Path) -> FleetConfig:
    config_path = Path(path).expanduser().resolve(strict=False)
    try:
        raw_bytes = config_path.read_bytes()
    except OSError as exc:
        raise FleetConfigError(f"cannot read fleet config {config_path}: {exc}") from exc
    try:
        parsed = yaml.safe_load(raw_bytes) or {}
    except yaml.YAMLError as exc:
        raise FleetConfigError(f"invalid YAML in {config_path}: {exc}") from exc
    raw = _require_mapping(parsed, field="root")
    _reject_unknown(
        raw,
        field="root",
        allowed={"version", "source", "promotion", "policy", "snapshots", "entities"},
    )
    version = raw.get("version")
    if version != SCHEMA_VERSION:
        raise FleetConfigError(
            f"unsupported fleet config version {version!r}; expected {SCHEMA_VERSION}"
        )

    source = _parse_source(raw)
    promotion = _parse_promotion(raw)
    policy = _parse_policy(raw)
    snapshot_root = _parse_snapshot_root(raw)
    entities = _parse_entities(raw)
    _validate_cross_entity_isolation(source, entities, snapshot_root)
    return FleetConfig(
        path=config_path,
        source=source,
        promotion=promotion,
        policy=policy,
        entities=entities,
        snapshot_root=snapshot_root,
        raw_sha256=hashlib.sha256(raw_bytes).hexdigest(),
    )
