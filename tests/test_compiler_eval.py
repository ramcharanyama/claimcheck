"""Offline tests for the policy compiler evaluation harness."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import pytest

from benchmark.compiler_eval import (
    GOLD_RULEBOOK_PATH,
    SYNTHETIC_POLICY_PATH,
    format_compiler_markdown,
    load_pricing_table,
    run_compiler_eval,
)
from src.documents.policy_document import load_document
from src.schemas import Rulebook

ROOT_DIR = Path(__file__).resolve().parent.parent


def test_compiler_eval_mock_mode_run(tmp_path: Path):
    """Verify compiler evaluation in mock mode produces valid metrics and reports."""
    out_json = tmp_path / "compiler_test.json"
    json_path, md_path, report = run_compiler_eval(
        strategy="mock",
        policy_path=SYNTHETIC_POLICY_PATH,
        gold_path=GOLD_RULEBOOK_PATH,
        out_path=out_json,
    )

    assert json_path.exists()
    assert md_path.exists()

    # Verify report metadata
    meta = report.metadata
    assert "git_commit_hash" in meta
    assert meta["total_gold_rules"] == 11
    assert meta["total_compiled_rules"] >= 1

    # Verify metrics
    m = report.metrics
    assert 0.0 <= m["recall"] <= 1.0
    assert 0.0 <= m["precision"] <= 1.0
    assert 0.0 <= m["parameter_accuracy"] <= 1.0
    assert 0.0 <= m["citation_accuracy"] <= 1.0
    assert 0.0 <= m["page_accuracy"] <= 1.0
    assert m["cost_usd"] > 0.0
    assert m["token_usage"]["total_tokens"] > 0

    # Verify per-rule diffs
    assert len(report.per_rule_diffs) == 11
    for diff in report.per_rule_diffs:
        assert "gold_rule_id" in diff
        assert "rule_type" in diff
        assert "parameter_diffs" in diff

    # Verify ambiguities evaluation
    assert "expected_ambiguities" in report.ambiguities_eval


def test_compiler_eval_cli(tmp_path: Path):
    """Test invoking compiler_eval.py via CLI."""
    out_json = tmp_path / "cli_compiler_test.json"
    cmd = [
        sys.executable,
        str(ROOT_DIR / "benchmark" / "compiler_eval.py"),
        "--strategy", "mock",
        "--out", str(out_json),
    ]

    res = subprocess.run(cmd, capture_output=True, text=True)
    assert res.returncode == 0, f"CLI error: {res.stderr}"
    assert out_json.exists()
    data = json.loads(out_json.read_text(encoding="utf-8"))
    assert "metrics" in data
    assert "per_rule_diffs" in data
