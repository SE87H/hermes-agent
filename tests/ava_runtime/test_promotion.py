from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

from hermes_cli.ava_runtime.fleet_config import load_fleet_config

ROOT = Path(__file__).resolve().parents[2]
PROMOTION_PATH = ROOT / "scripts" / "ava_runtime" / "promotion.py"


def _load_module():
    name = "ava_runtime_promotion_test"
    spec = importlib.util.spec_from_file_location(name, PROMOTION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _config(tmp_path: Path):
    raw = {
        "version": 1,
        "snapshots": {"root": str(tmp_path / "backups")},
        "promotion": {"upstream_snapshot": "ava/upstream-2026-07-30", "staging": "ava/staging", "stable": "ava/stable", "require_shadow_validation": True, "require_rollback_ref": True},
        "policy": {"explicit_hermes_home": "required", "resume_missing_session": "fail", "resume_missing_workspace": "fail", "resume_ambiguous_lineage": "fail", "restore_recorded_workspace": True, "allow_restore_workspace_opt_out": True, "global_most_recent_session": "forbidden_for_managed_entities", "terminal_backend_failure": "fail", "deployment_ref": "pinned_commit_or_stable_tag", "secrets_in_repository": "forbidden"},
        "source": {"repository": str(tmp_path / "source"), "expected_ref": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "require_clean_checkout": True, "auto_update": False},
        "entities": {},
    }
    for entity in ("ava", "aeon", "avaeon-codex"):
        raw["entities"][entity] = {"hermes_home": str(tmp_path / "state" / entity), "workspace": str(tmp_path / "workspaces" / entity), "profile": entity, "session_scope": entity}
    path = tmp_path / "fleet.yaml"
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return load_fleet_config(path)


def _report() -> dict:
    entity_gate = {"doctor": {"status": "pass"}, "identity_smoke": {"status": "pass"}, "shadow_runtime": {"status": "pass"}}
    return {
        "schema_version": 1,
        "status_closure": "pass",
        "candidate_commit": "dddddddddddddddddddddddddddddddddddddddd",
        "rollback_ref": "stable-previous",
        "tests": {"ava_runtime": {"status": "pass", "command": "pytest tests/ava_runtime"}, "hermes_cli": {"status": "pass", "command": "pytest tests/hermes_cli"}, "upstream_relevant": {"status": "pass", "command": "pytest relevant"}},
        "entities": {"ava": dict(entity_gate), "aeon": dict(entity_gate), "avaeon-codex": dict(entity_gate)},
        "remaining_gaps": [],
    }


def _fake_git(repo: Path, *args: str) -> str:
    if args == ("rev-parse", "HEAD"):
        return "dddddddddddddddddddddddddddddddddddddddd"
    if args == ("rev-parse", "HEAD^{tree}"):
        return "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee"
    if args == ("rev-parse", "--verify", "stable-previous^{commit}"):
        return "ffffffffffffffffffffffffffffffffffffffff"
    if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
        return ""
    raise AssertionError(args)


def test_build_manifest_closes_candidate_and_rollback(monkeypatch, tmp_path):
    module = _load_module()
    config = _config(tmp_path)
    report_path = tmp_path / "report.json"
    report = _report()
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(module, "_git", _fake_git)
    manifest = module.build_manifest(config=config, report_path=report_path, report=report, repo=config.source.repository)
    assert manifest["status_closure"] == "pass"
    assert manifest["source"]["candidate_commit"] == "dddddddddddddddddddddddddddddddddddddddd"
    assert manifest["source"]["rollback_commit"] == "ffffffffffffffffffffffffffffffffffffffff"
    assert manifest["evidence"]["fleet_config_sha256"] == config.raw_sha256
    assert set(manifest["evidence"]["entities"]) == {"ava", "aeon", "avaeon-codex"}


def test_report_rejects_any_failed_entity_gate():
    module = _load_module()
    report = _report()
    report["entities"]["aeon"]["shadow_runtime"] = {"status": "fail"}
    with pytest.raises(module.PromotionError, match="shadow_runtime"):
        module._validate_report(report)


def test_report_requires_all_three_entities():
    module = _load_module()
    report = _report()
    del report["entities"]["ava"]
    with pytest.raises(module.PromotionError, match="exactly"):
        module._validate_report(report)


def test_report_rejects_remaining_gaps():
    module = _load_module()
    report = _report()
    report["remaining_gaps"] = ["real model smoke not run"]
    with pytest.raises(module.PromotionError, match="remaining_gaps"):
        module._validate_report(report)


def test_candidate_must_match_checked_out_head(monkeypatch, tmp_path):
    module = _load_module()
    config = _config(tmp_path)
    report = _report()
    report["candidate_commit"] = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(module, "_git", _fake_git)
    with pytest.raises(module.PromotionError, match="does not match"):
        module.build_manifest(config=config, report_path=report_path, report=report, repo=config.source.repository)


def test_dirty_tree_blocks_promotion(monkeypatch, tmp_path):
    module = _load_module()
    def dirty_git(repo: Path, *args: str) -> str:
        if args == ("status", "--porcelain=v1", "--untracked-files=normal"):
            return " M modified.py"
        return _fake_git(repo, *args)
    monkeypatch.setattr(module, "_git", dirty_git)
    with pytest.raises(module.PromotionError, match="working tree must be clean"):
        module._git_state(tmp_path, "stable-previous")


def test_verify_detects_report_hash_drift(monkeypatch, tmp_path):
    module = _load_module()
    config = _config(tmp_path)
    report = _report()
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report), encoding="utf-8")
    monkeypatch.setattr(module, "_git", _fake_git)
    manifest = module.build_manifest(config=config, report_path=report_path, report=report, repo=config.source.repository)
    report_path.write_text(json.dumps({**report, "note": "changed"}), encoding="utf-8")
    with pytest.raises(module.PromotionError, match="validation_report_sha256"):
        module.verify_manifest(manifest=manifest, config=config, report_path=report_path, report=report, repo=config.source.repository)


def test_atomic_write_replaces_manifest(tmp_path):
    module = _load_module()
    path = tmp_path / "manifest.json"
    module._atomic_write_json(path, {"value": 1})
    module._atomic_write_json(path, {"value": 2})
    assert json.loads(path.read_text(encoding="utf-8")) == {"value": 2}
    assert path.stat().st_mode & 0o777 == 0o644
