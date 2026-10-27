"""Drives app.py headlessly (Streamlit AppTest) through the live-mode approval flow
with a fake Nemotron client: compile -> review -> approve -> analyze. No network."""

from __future__ import annotations

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest  # noqa: E402

from tests.test_ui_pipeline import _FakeNemotron  # noqa: E402

from pathlib import Path  # noqa: E402

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


@pytest.fixture
def live_env(monkeypatch):
    monkeypatch.setenv("NEBIUS_API_KEY", "dummy-live-key")
    monkeypatch.setenv("NEBIUS_MODEL_FAST", "fake-nemotron-fast")
    monkeypatch.setenv("NEBIUS_MODEL_REASONING", "fake-nemotron-reasoning")
    fake = _FakeNemotron("packet_08")
    monkeypatch.setattr("src.ai.policy_compiler.get_client", lambda: fake)
    monkeypatch.setattr("src.ai.claim_extractor.get_client", lambda: fake)
    return fake


def _start_live(at: AppTest) -> AppTest:
    at.run(timeout=60)
    live_label = next(o for o in at.sidebar.radio[0].options if o.startswith("Live Mode (NVIDIA"))
    at.sidebar.radio[0].set_value(live_label).run(timeout=60)
    at.sidebar.selectbox[0].set_value("packet_08").run(timeout=60)
    return at


def _button(at: AppTest, label_start: str):
    return next(b for b in at.button if b.label.startswith(label_start))


def _finding_counts(at: AppTest):
    return {m.label: int(m.value) for m in at.metric if m.label in ("Violated Rules", "Needs Review")}


def test_live_flow_requires_approval_then_applies_rules(live_env):
    at = _start_live(AppTest.from_file(str(APP_PATH), default_timeout=60))

    _button(at, "1. Compile policy rules").click().run(timeout=60)
    assert at.session_state["compilation"] is not None
    assert any("Review compiled policy rules" in h.value for h in at.subheader)
    assert len(at.checkbox) == 5
    assert all(cb.value for cb in at.checkbox)  # verified rules are pre-ticked for review

    # Run before approving: everything is needs_review
    _button(at, "Run Adjudication Analysis").click().run(timeout=60)
    assert _finding_counts(at)["Violated Rules"] == 0

    # Approve, then run again: the non-payable rule is applied
    _button(at, "Approve selected rules").click().run(timeout=60)
    assert len(at.session_state["approved_rule_ids"]) == 5
    _button(at, "Run Adjudication Analysis").click().run(timeout=60)
    assert _finding_counts(at)["Violated Rules"] >= 1
    assert at.session_state["result"].approved_rule_ids


def test_unticked_rule_is_not_approved(live_env):
    at = _start_live(AppTest.from_file(str(APP_PATH), default_timeout=60))
    _button(at, "1. Compile policy rules").click().run(timeout=60)
    next(cb for cb in at.checkbox if cb.key == "approve_RULE_NON_PAYABLE_ITEMS").uncheck()
    _button(at, "Approve selected rules").click().run(timeout=60)
    assert "RULE_NON_PAYABLE_ITEMS" not in at.session_state["approved_rule_ids"]
    _button(at, "Run Adjudication Analysis").click().run(timeout=60)
    assert _finding_counts(at)["Violated Rules"] == 0
