"""Per-run guardrails — fail fast so a bulk run can't silently overrun item or spend caps.
Pre-run estimates only (conservative), not live billing."""

# Rough, deliberately conservative pre-run estimate. ~4 chars/token; a single blended
# input+output USD/Mtok constant. This is a sanity gate, not an invoice — err high.
_CHARS_PER_TOKEN = 4
_USD_PER_MTOK = 15.0  # conservative blended rate; real pricing varies by model


def check_item_cap(planned: int, run_cfg) -> None:
    """Raise SystemExit if planned item count exceeds guardrails_max_items_per_run."""
    cap = run_cfg.guardrails_max_items_per_run
    if cap is not None and planned > cap:
        raise SystemExit(
            f"guardrails: planned {planned} items exceeds max_items_per_run={cap}. "
            f"Lower the run scope or raise the cap in run.json.")


def estimate_spend_usd(char_total: int, run_cfg) -> float:
    """Rough conservative pre-run USD estimate for the api backend from total prompt chars."""
    tokens = char_total / _CHARS_PER_TOKEN
    return tokens / 1_000_000 * _USD_PER_MTOK


def check_spend_cap(char_total: int, run_cfg) -> None:
    """Raise SystemExit if the estimated api spend exceeds guardrails_max_spend_usd.
    No-op for non-api backends (no per-token cost) and when the cap is None."""
    if run_cfg.enrich.backend != "api":
        return
    cap = run_cfg.guardrails_max_spend_usd
    if cap is None:
        return
    est = estimate_spend_usd(char_total, run_cfg)
    if est > cap:
        raise SystemExit(
            f"guardrails: estimated api spend ${est:.2f} exceeds max_spend_usd=${cap:.2f}. "
            f"Lower the batch/run scope or raise the cap in run.json.")


def usage_reminder(run_cfg) -> str | None:
    """A printed Claude-Max usage reminder for session backends (can't be checked
    programmatically). None for local/api."""
    if run_cfg.enrich.backend in ("claude-code", "cowork"):
        return ("reminder: this run uses a Claude subscription session — watch your "
                "Claude-Max usage; large batches may hit limits mid-run.")
    return None
