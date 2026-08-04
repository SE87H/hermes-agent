from __future__ import annotations

import pytest

from hermes_cli.ava_runtime.update_policy import (
    AUTO_UPDATE,
    assert_live_update_forbidden,
    assert_pinned_source,
)


def test_live_update_is_disabled_and_self_update_fails_closed():
    assert AUTO_UPDATE is False
    with pytest.raises(RuntimeError, match="self-update is forbidden"):
        assert_live_update_forbidden(["hermes", "update"])


def test_moving_source_is_not_a_live_source():
    with pytest.raises(ValueError, match="exact commit SHA"):
        assert_pinned_source("ava/stable")
    assert assert_pinned_source("A" * 40) == "a" * 40


def test_other_commands_are_not_update_commands():
    assert_live_update_forbidden(["hermes", "--resume", "session"])
