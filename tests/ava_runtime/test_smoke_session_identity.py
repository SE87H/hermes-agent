from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[2]
SMOKE_PATH = ROOT / "scripts" / "ava_runtime" / "smoke_session_identity.py"


def _load_smoke_module():
    module_name = "ava_runtime_smoke_test"
    spec = importlib.util.spec_from_file_location(module_name, SMOKE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_managed_commands_do_not_use_upstream_z_surface(tmp_path):
    mod = _load_smoke_module()
    args = SimpleNamespace(mode="managed")
    command = ["python", "-m", "hermes_cli.ava_runtime.managed_oneshot"]
    usage = tmp_path / "usage.json"

    first = mod._first_command(args, command, usage, "prompt")
    second = mod._resume_command(args, command, "session-1", usage, "prompt")

    assert "-z" not in first
    assert "-z" not in second
    assert first[-1] == "prompt"
    assert second[-1] == "prompt"
    assert second[3:5] == ["--resume", "session-1"]


def test_upstream_comparison_commands_use_z_surface(tmp_path):
    mod = _load_smoke_module()
    args = SimpleNamespace(mode="upstream")
    command = ["hermes"]
    usage = tmp_path / "usage.json"

    first = mod._first_command(args, command, usage, "prompt")
    second = mod._resume_command(args, command, "session-1", usage, "prompt")

    assert "-z" in first
    assert "-z" in second
    assert ["--resume", "session-1"] == second[1:3]


def test_default_command_targets_managed_launcher(monkeypatch):
    monkeypatch.delenv("AVA_ENTITY", raising=False)
    mod = _load_smoke_module()
    args = mod.build_parser().parse_args([])

    assert args.mode == "managed"
    assert "hermes_cli.ava_runtime.managed_oneshot" in args.hermes_command
    assert args.entity == "aeon"
