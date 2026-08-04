"""Fail-closed policy for live Hermes updates.

The operator may prepare and validate pinned candidates, but no live process may
update itself or resolve a moving branch.
"""

from __future__ import annotations

from collections.abc import Sequence

AUTO_UPDATE = False
FORBIDDEN_LIVE_COMMANDS = frozenset({"hermes update", "update"})


def assert_live_update_forbidden(command: Sequence[str]) -> None:
    normalized = " ".join(str(part).strip().lower() for part in command if str(part).strip())
    if normalized in FORBIDDEN_LIVE_COMMANDS or normalized.endswith(" hermes update"):
        raise RuntimeError("live self-update is forbidden; use an isolated pinned candidate")


def assert_pinned_source(ref: str) -> str:
    value = ref.strip()
    if len(value) != 40 or any(char not in "0123456789abcdefABCDEF" for char in value):
        raise ValueError("live source must be an exact commit SHA, never a moving branch")
    return value.lower()
