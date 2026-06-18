"""Tests for per-run guardrails — item cap, spend estimate, and usage reminders."""
from types import SimpleNamespace

import pytest

from insta_save.orchestrator.guardrails import (
    check_item_cap,
    check_spend_cap,
    estimate_spend_usd,
    usage_reminder,
)


# ---------------------------------------------------------------------------
# Helpers to build minimal fake run_cfg
# ---------------------------------------------------------------------------

def _cfg(backend="api", max_items=None, max_spend=None):
    enrich = SimpleNamespace(backend=backend)
    return SimpleNamespace(
        guardrails_max_items_per_run=max_items,
        guardrails_max_spend_usd=max_spend,
        enrich=enrich,
    )


# ---------------------------------------------------------------------------
# check_item_cap
# ---------------------------------------------------------------------------

class TestCheckItemCap:
    def test_over_cap_raises(self):
        cfg = _cfg(max_items=20)
        with pytest.raises(SystemExit) as exc_info:
            check_item_cap(50, cfg)
        assert "20" in str(exc_info.value)
        assert "50" in str(exc_info.value)

    def test_exactly_at_cap_is_ok(self):
        """Boundary: planned == cap is NOT over — should not raise."""
        cfg = _cfg(max_items=20)
        check_item_cap(20, cfg)  # must not raise

    def test_under_cap_is_ok(self):
        cfg = _cfg(max_items=20)
        check_item_cap(10, cfg)  # must not raise

    def test_no_cap_always_ok(self):
        cfg = _cfg(max_items=None)
        check_item_cap(1_000_000, cfg)  # must not raise


# ---------------------------------------------------------------------------
# estimate_spend_usd
# ---------------------------------------------------------------------------

class TestEstimateSpendUsd:
    def test_returns_positive_float(self):
        cfg = _cfg()
        result = estimate_spend_usd(100_000, cfg)
        assert isinstance(result, float)
        assert result > 0

    def test_known_value(self):
        # 4_000_000 chars / 4 chars_per_token = 1_000_000 tokens = 1 Mtok * $15 = $15.0
        cfg = _cfg()
        assert estimate_spend_usd(4_000_000, cfg) == pytest.approx(15.0)

    def test_linear_scaling(self):
        cfg = _cfg()
        est_1x = estimate_spend_usd(1_000_000, cfg)
        est_2x = estimate_spend_usd(2_000_000, cfg)
        assert est_2x == pytest.approx(2 * est_1x)


# ---------------------------------------------------------------------------
# check_spend_cap
# ---------------------------------------------------------------------------

class TestCheckSpendCap:
    def test_api_over_cap_raises(self):
        # 4_000_000 chars → $15.0; cap=$1.0 → should raise
        cfg = _cfg(backend="api", max_spend=1.0)
        with pytest.raises(SystemExit) as exc_info:
            check_spend_cap(4_000_000, cfg)
        assert "1.00" in str(exc_info.value)

    def test_api_under_cap_is_ok(self):
        # 4_000 chars → tiny spend; cap=$100.0 → should NOT raise
        cfg = _cfg(backend="api", max_spend=100.0)
        check_spend_cap(4_000, cfg)

    def test_api_no_cap_is_ok(self):
        cfg = _cfg(backend="api", max_spend=None)
        check_spend_cap(4_000_000, cfg)  # must not raise

    def test_local_non_api_no_raise(self):
        # Non-api backend — no per-token cost; ignore cap entirely
        cfg = _cfg(backend="local", max_spend=0.01)
        check_spend_cap(4_000_000, cfg)  # must not raise

    def test_claude_code_non_api_no_raise(self):
        cfg = _cfg(backend="claude-code", max_spend=0.01)
        check_spend_cap(4_000_000, cfg)  # must not raise

    def test_cowork_non_api_no_raise(self):
        cfg = _cfg(backend="cowork", max_spend=0.01)
        check_spend_cap(4_000_000, cfg)  # must not raise


# ---------------------------------------------------------------------------
# usage_reminder
# ---------------------------------------------------------------------------

class TestUsageReminder:
    def test_claude_code_returns_reminder(self):
        cfg = _cfg(backend="claude-code")
        result = usage_reminder(cfg)
        assert result is not None
        assert len(result) > 0

    def test_cowork_returns_reminder(self):
        cfg = _cfg(backend="cowork")
        result = usage_reminder(cfg)
        assert result is not None
        assert len(result) > 0

    def test_local_returns_none(self):
        cfg = _cfg(backend="local")
        assert usage_reminder(cfg) is None

    def test_api_returns_none(self):
        cfg = _cfg(backend="api")
        assert usage_reminder(cfg) is None

    def test_claude_p_returns_reminder(self):
        cfg = _cfg(backend="claude-p")
        result = usage_reminder(cfg)
        assert result is not None
        assert len(result) > 0
