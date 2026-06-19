"""isa — Insta-Save v2 CLI entrypoint. Arg surface only; stages are stubbed until built."""

import argparse
import os
from datetime import datetime
from pathlib import Path

from insta_save.config.env import load_env as _load_env
from insta_save.config.run import load_run_config as _load_run
from insta_save.config.collections import load_collections as _load_collections
from insta_save.config.tags import load_vocab
from insta_save.adapters.notion import ensure_schema
from insta_save.helpers.observability import StageProgress, setup_logging
from insta_save.stages.extract import run_extract_stage
from insta_save.stages import enrich
from insta_save.orchestrator.calibrate_gate import run_calibrate_gate
from insta_save.backup import backup, restore_check
from insta_save.backends.base import get_backend
from insta_save.config.routes import load_routes
from insta_save.orchestrator import guardrails
from insta_save.orchestrator.preflight import preflight
from insta_save.orchestrator.status_report import build_status, retry_failed as _retry_failed
from insta_save.orchestrator.config_gate import ensure_run_json, run_config_gate

STAGES = ["discover", "ingest", "select", "extract", "calibrate", "enrich", "deterministic", "route"]


def _rel(p: str) -> str:
    """Return a relative form of an absolute log path for readable console output."""
    return os.path.relpath(str(p))

_STATUS_COLS = ("Imported", "Queued", "Extracted", "Tagged", "Routed", "Failed", "remaining")


def _print_status_table(rows: list[dict]) -> None:
    """Render per-group pipeline counts using rich.Table."""
    from rich.table import Table
    from rich.console import Console

    table = Table(title="Pipeline status", show_header=True, header_style="bold")
    table.add_column("Group", style="cyan", no_wrap=True)
    for col in _STATUS_COLS:
        table.add_column(col, justify="right")

    for row in rows:
        is_total = row["group"] == "TOTAL"
        style = "bold" if is_total else None
        table.add_row(
            row["group"],
            *[str(row.get(c, 0)) for c in _STATUS_COLS],
            style=style,
        )

    Console().print(table)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="isa", description="Insta-Save v2 pipeline.")
    sub = p.add_subparsers(dest="command", required=True)

    disc = sub.add_parser("discover", help="Surface collections + configure / diff + crawl grids.")
    disc.add_argument("--headed", action="store_true")
    disc.add_argument("--fresh", action="store_true", help="Ignore reusable snapshots.")
    disc.add_argument("--collection", default=None, help="Limit crawl to one collection.")
    disc.add_argument("--ig-username", default=None, help="Override IG_USERNAME from env.")

    run = sub.add_parser("run", help="Run the pipeline (a mode, or a single stage).")
    run.add_argument("--mode", choices=["first-time", "incremental"], default="incremental")
    run.add_argument("--stage", choices=STAGES, default=None)
    run.add_argument("--group", default=None)
    run.add_argument("--limit", type=int, default=None)
    run.add_argument("--reextract", action="store_true")
    run.add_argument("--retry-failed", action="store_true")
    run.add_argument("--prepare", action="store_true")
    run.add_argument("--apply", action="store_true")
    run.add_argument("--calibrate-limit", type=int, default=20)
    run.add_argument("--collection", default=None)
    run.add_argument("--fresh", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--headed", action="store_true")
    run.add_argument("--confirm-removed", action="append", default=None)
    run.add_argument("--lane", choices=["text", "vision"], default="text")
    run.add_argument("--status", action="store_true")
    run.add_argument("--select-mode", choices=["inline", "editor"], default="inline")

    st = sub.add_parser("status", help="Per-group counts: imported/extracted/tagged/failed/left.")
    st.add_argument("--retry-failed", action="store_true",
                    help="Requeue all Failed items back to their inferred prior status.")
    bk = sub.add_parser("backup", help="Snapshot Notion to JSON.")
    bk.add_argument("--restore-check", action="store_true")
    return p


def dispatch_run(args) -> None:
    if args.stage == "extract":
        env = _load_env()
        run_cfg = _load_run()
        collections_cfg = _load_collections()
        ensure_schema(env)
        log_path = setup_logging("extract")
        print(f"Logging to {_rel(log_path)}")
        with StageProgress("Extract") as progress:
            run_extract_stage(
                env, run_cfg.extract, progress,
                limit=args.limit, group=args.group,
                collections_cfg=collections_cfg, reextract=args.reextract)
        return
    if args.stage == "calibrate":
        # Standalone interactive vocab editor (same gate the first-time loop runs inline):
        # sample -> backend drafts -> you reject/add + preview -> lock into config/tags.json.
        if not args.group:
            raise SystemExit("isa run --stage calibrate: --group is required")
        env = _load_env()
        run_cfg = _load_run()
        collections_cfg = _load_collections()
        backend = get_backend(run_cfg.enrich.backend)
        print(f"Logging to {_rel(setup_logging('calibrate'))}")
        run_calibrate_gate(env, run_cfg, collections_cfg=collections_cfg,
                           backend=backend, group=args.group)
        return

    if args.stage == "enrich":
        from insta_save.backends import base
        # validate args BEFORE loading config/vocab (fail fast; agent-filled --apply reads
        # the group from batch.json, so it needs no --group). load_vocab() must not run for a
        # bad-args prepare — config/tags.json may not exist until calibrate has locked it.
        env = _load_env()
        run_cfg = _load_run()
        backend = base.get_backend(run_cfg.enrich.backend)
        budgets = backend.batch_budgets(run_cfg)

        # Vision preflight: a backend that can't see images must never run the vision lane.
        if args.lane == "vision" and not backend.VISION_CAPABLE:
            raise SystemExit(f"enrich backend {backend.NAME!r} is not vision-capable; "
                             "the vision lane requires a vision-capable backend")

        # --status: backend-agnostic remaining-enrichable count (a Notion query via cowork).
        if args.status:
            if not args.group:
                raise SystemExit("isa run --stage enrich --status: --group is required")
            from insta_save.backends import cowork
            collections_cfg = _load_collections()
            print(f"{args.group}: {cowork.status(env, collections_cfg, args.group)} "
                  f"enrichable remaining")
            return

        statuses = ["Extracted"]
        if args.lane == "vision":
            kinds = {"Carousel", "Post"}
            template = Path("prompts/enrich_vision_v2.0.txt").read_text(encoding="utf-8")
            image_budget = budgets.image_token_budget
        else:
            kinds = {"Reel", "IGTV"}
            template = Path("prompts/enrich_v2.0.txt").read_text(encoding="utf-8")
            image_budget = None

        if backend.AUTOMATED:
            # local/api fill results.json in-process, so one invocation DRAINS the group:
            # drain_enrich_group loops prepare→fill→apply per lane until drained.
            # --prepare/--apply are ignored here (drain handles both). --group is required.
            if not args.group:
                raise SystemExit("isa run --stage enrich: --group is required for the "
                                 f"{backend.NAME!r} backend (drains the group in one run)")
            vocab = load_vocab()
            collections_cfg = _load_collections()
            log_path = setup_logging("enrich")
            print(f"Logging to {_rel(log_path)}")
            totals = enrich.drain_enrich_group(
                env, run_cfg, collections_cfg, vocab, backend, args.group,
                lanes=[args.lane],
            )
            for lane_name, lane_info in totals["lanes"].items():
                if lane_info["stop_reason"] == "drained":
                    print(f"ENRICH_DRAINED group={args.group} lane={lane_name}")
                else:
                    print(f"enrich: no items applied for group {args.group} (lane={lane_name}) — "
                          f"stopping to avoid a no-progress loop. Check logs; resolve failures, "
                          f"then re-run.")
            return

        # agent-filled backends (claude-code/cowork): one prepare or apply step; the driving
        # session (or Cowork loop) runs the fill and re-invokes. --apply reads group from batch.json.
        if args.prepare and args.apply:
            raise SystemExit("isa run --stage enrich: pass exactly one of --prepare/--apply")
        if not args.apply and not args.group:
            raise SystemExit("isa run --stage enrich --prepare: --group is required")
        vocab = load_vocab()
        if args.apply:
            log_path = setup_logging("enrich-apply")
            print(f"Logging to {_rel(log_path)}")
            collections_cfg = _load_collections()
            with StageProgress("Enrich apply") as progress:
                counts = enrich.apply(env, vocab=vocab, model=run_cfg.enrich.model,
                                      collections_cfg=collections_cfg, progress=progress)
            print(f"Applied: {counts['written']} written, {counts['failed']} failed.")
            return
        # prepare (group guaranteed present by the guard above)
        log_path = setup_logging("enrich-prepare")
        print(f"Logging to {_rel(log_path)}")
        collections_cfg = _load_collections()
        with StageProgress("Enrich prepare") as progress:
            n = enrich.prepare(env, group=args.group, collections_cfg=collections_cfg, vocab=vocab,
                               char_budget=budgets.char_budget, max_items=budgets.max_items,
                               statuses=statuses, prompt_template=template,
                               kinds=kinds, image_token_budget=image_budget,
                               output_language=run_cfg.output_language, progress=progress)
        if n == 0:
            print(f"No items left to enrich in group {args.group} (lane={args.lane}).")
            # Stable machine token so an unattended prepare->fill->apply loop can detect
            # a drained group without matching the prose above.
            print(f"ENRICH_DRAINED group={args.group} lane={args.lane}")
        else:
            print(f"Prepared {n} items (lane={args.lane}) -> tmp/enrich/prompt.txt. In a Claude session: "
                  f'"Read tmp/enrich/prompt.txt and write tmp/enrich/results.json", '
                  f"then: isa run --stage enrich --apply")
        return

    if args.stage == "ingest":
        env = _load_env()
        collections_cfg = _load_collections()
        ensure_schema(env)
        log_path = setup_logging("ingest")
        print(f"Logging to {_rel(log_path)}")
        from insta_save.stages.ingest import run_ingest
        names = [args.collection] if getattr(args, "collection", None) else None
        confirmed = set(args.confirm_removed or [])
        with StageProgress("Ingest") as progress:
            r = run_ingest(env, collections_cfg=collections_cfg, names=names,
                           confirmed_removed=confirmed, headed=args.headed,
                           dry_run=args.dry_run, progress=progress)
        print(f"Ingest: {r['created']} created, {r['retagged']} retagged, "
              f"{r['backfilled']} backfilled, {r['degraded']} degraded, "
              f"{r['skipped_unsafe']} unsafe-skipped"
              f"{' (dry-run)' if args.dry_run else ''}.")
        return

    if args.stage == "select":
        env = _load_env()
        collections_cfg = _load_collections()
        log_path = setup_logging("select")
        print(f"Logging to {_rel(log_path)}")
        from insta_save.stages.select import run_select_stage
        with StageProgress("Select") as progress:
            r = run_select_stage(env, collections_cfg, progress,
                                 limit=args.limit, group=args.group,
                                 write_delay=env.notion_write_delay)
        print(f"Select: {r.get('queued', 0)} → Queued, "
              f"{r.get('deterministic_pending', 0)} left Imported (deterministic branch).")
        return

    if args.stage == "deterministic":
        env = _load_env()
        run_cfg = _load_run()
        collections_cfg = _load_collections()
        ensure_schema(env)
        from insta_save.stages import deterministic as det
        if run_cfg.deterministic_title_mode == "llm":
            # Deterministic titling reuses the enrich backend config — the user runs ONE
            # backend per run. Automated backends (local/api) fill the title batch
            # in-process via the enrich fill (which over-produces summary/tags fields the
            # deterministic apply ignores — harmless; det.apply consumes only `title`).
            from insta_save.backends import base
            backend = base.get_backend(run_cfg.enrich.backend)
            template = Path(f"prompts/{det.PROMPT_VERSION}.txt").read_text(encoding="utf-8")

            if backend.AUTOMATED:
                # local/api fill results.json in-process, so one invocation DRAINS the group:
                # prepare -> fill -> apply, looped until prepare batches no LLM-title items.
                if not args.group:
                    raise SystemExit("isa run --stage deterministic: --group is required for the "
                                     f"{backend.NAME!r} backend (drains the group in one run)")
                log_path = setup_logging("deterministic")
                print(f"Logging to {_rel(log_path)}")
                det_dir = Path(env.tmp_dir) / "deterministic"
                while True:
                    with StageProgress("Deterministic prepare") as progress:
                        r = det.prepare(env, group=args.group, collections_cfg=collections_cfg,
                                        language=run_cfg.output_language, prompt_template=template,
                                        max_items=run_cfg.max_items, progress=progress)
                    if r["batched"] == 0:
                        print(f"DETERMINISTIC_DRAINED group={args.group}")
                        break
                    backend.fill(env, run_cfg, det_dir)
                    with StageProgress("Deterministic apply") as progress:
                        counts = det.apply(env, progress=progress)
                    print(f"Applied: {counts['written']} written, {counts['failed']} failed.")
                    # No-progress guard: prepare batched items but apply wrote none, so those
                    # items stay Imported and the next prepare re-selects them — would spin
                    # forever. Stop instead.
                    if counts["written"] == 0:
                        print(f"deterministic: no items applied for group {args.group} — "
                              f"stopping to avoid a no-progress loop.")
                        break
                return

            # agent-filled backends (claude-code/cowork): one prepare or apply step; the
            # driving session (or Cowork loop) runs the fill and re-invokes.
            if not (args.prepare or args.apply):
                raise SystemExit("isa run --stage deterministic: title_mode=llm requires "
                                 "--prepare or --apply")
            if args.prepare and args.apply:
                raise SystemExit("isa run --stage deterministic: pass exactly one of --prepare/--apply")
            if args.apply:
                log_path = setup_logging("deterministic-apply")
                print(f"Logging to {_rel(log_path)}")
                with StageProgress("Deterministic apply") as progress:
                    counts = det.apply(env, progress=progress)
                print(f"Applied: {counts['written']} written, {counts['failed']} failed.")
                return
            if not args.group:
                raise SystemExit("isa run --stage deterministic --prepare: --group is required")
            log_path = setup_logging("deterministic-prepare")
            print(f"Logging to {_rel(log_path)}")
            with StageProgress("Deterministic prepare") as progress:
                r = det.prepare(env, group=args.group, collections_cfg=collections_cfg,
                                language=run_cfg.output_language, prompt_template=template,
                                max_items=run_cfg.max_items, progress=progress)
            if r["batched"] == 0:
                print(f"No caption-bearing items to title in group {args.group} "
                      f"({r['finalized_template']} finalized with template titles).")
            else:
                print(f"Prepared {r['batched']} titles -> tmp/deterministic/prompt.txt "
                      f"({r['finalized_template']} finalized, no caption). In a Claude session: "
                      f'"Read tmp/deterministic/prompt.txt and write tmp/deterministic/results.json", '
                      f"then: isa run --stage deterministic --apply")
            return
        # template mode (one-shot)
        log_path = setup_logging("deterministic")
        print(f"Logging to {_rel(log_path)}")
        with StageProgress("Deterministic") as progress:
            r = det.run_deterministic_stage(env, collections_cfg, progress,
                                            limit=args.limit, group=args.group,
                                            write_delay=env.notion_write_delay)
        print(f"Deterministic: {r.get('tagged', 0)} → Tagged, "
              f"{r.get('skipped_extract_path', 0)} skipped (extract path).")
        return

    if args.stage == "route":
        env = _load_env()
        collections_cfg = _load_collections()
        ensure_schema(env)
        from insta_save.stages.route import run_route_stage
        routes = load_routes()
        log_path = setup_logging("route")
        print(f"Logging to {_rel(log_path)}")
        with StageProgress("Route") as progress:
            r = run_route_stage(env, routes, collections_cfg, progress,
                                limit=args.limit, group=args.group, dry_run=args.dry_run,
                                write_delay=0 if args.dry_run else env.notion_write_delay)
        print(f"Route: {r.get('routed', 0)} → Routed, {r.get('unrouted', 0)} left Tagged "
              f"(no mapping){' (dry-run)' if args.dry_run else ''}.")
        return

    raise SystemExit(f"isa run --stage {args.stage}: not implemented yet (v2 — see ARCHITECTURE.md)")


def _has_collections() -> bool:
    """True if config/collections.json exists (patchable in tests)."""
    return Path("config/collections.json").exists()


def _nested_progress(label: str):
    """Factory for nested (level=1) StageProgress bars used by the sequencer's per-group stages."""
    from insta_save.helpers.observability import StageProgress, RULE_NESTED
    return StageProgress(label, width=RULE_NESTED, level=1)


def _dispatch_mode(args) -> None:
    """Run the pipeline in first-time or incremental mode (no --stage given)."""
    env = _load_env()
    if args.mode == "first-time":
        ensure_run_json()                      # seed claude-p default if run.json absent
    run_cfg = _load_run()
    if args.mode == "first-time":
        run_cfg = run_config_gate(run_cfg, select_mode=getattr(args, "select_mode", "inline"))
    # True cold start: a first-time real run with no collections.json yet. The pipeline's
    # front-fold discover builds it, so don't require it up front — defer the load and skip
    # the pre-run item-cap guardrail (an unpopulated DB has nothing to cap; the guardrail
    # re-engages on resume once collections.json exists). dry-run still needs it (it plans
    # against current state with no discover), so it falls through to the clean-exit path.
    cold_start = args.mode == "first-time" and not args.dry_run and not _has_collections()
    if cold_start:
        collections_cfg = None
    else:
        try:
            collections_cfg = _load_collections()
        except RuntimeError as exc:
            raise SystemExit(str(exc))   # clean actionable exit, not an uncaught traceback

    vocab = load_vocab()
    backend = get_backend(run_cfg.enrich.backend)   # resolved AFTER the gate
    routes = load_routes()

    log_path = setup_logging("run")
    print(f"Logging to {_rel(log_path)}")

    # Preflight: backend reachable, Notion config present, engines importable, effort valid.
    preflight(env, run_cfg, stages={"extract", "enrich"})

    if not cold_start:
        # Guardrail cap: sum remaining (Imported+Queued+Extracted) across all groups as a
        # conservative pre-run planned count. Uses build_status (one query_all_pages pass) —
        # same cost as compute_plan's own query; no extra Notion round-trip.
        status_rows = build_status(env, collections_cfg)
        total_row = next((r for r in status_rows if r["group"] == "TOTAL"), None)
        planned = total_row["remaining"] if total_row else 0
        guardrails.check_item_cap(planned, run_cfg)

    reminder = guardrails.usage_reminder(run_cfg)
    if reminder:
        print(reminder)

    from insta_save.orchestrator.pipeline import run_pipeline
    ig_username = env.ig_username or os.environ.get("IG_USERNAME", "")
    plan = run_pipeline(env, run_cfg, collections_cfg, vocab, backend, routes,
                        mode=args.mode, dry_run=args.dry_run,
                        select_mode=getattr(args, "select_mode", "inline"),
                        ig_username=ig_username, headed=args.headed,
                        fresh=args.fresh, progress_factory=_nested_progress)

    _print_plan(plan, args.dry_run)


def _print_plan(plan, dry_run: bool) -> None:
    """Print the returned plan: one line per GroupStep, then a gate or done message."""
    label = " (dry-run)" if dry_run else ""
    for step in plan.steps:
        print(f"  {step.group}: {step.action} — {step.detail}")

    if plan.done:
        print(f"All groups complete.{label}")
        return

    na = plan.next_action
    if na is None:
        return

    if not na.automated:
        if na.action == "calibrate":
            # Reached only in --dry-run: a real --mode run handles calibrate inline
            # (the gate samples, the backend drafts, you lock — then enrich continues).
            print(
                f"\nNEXT{label}: {na.group} — {na.detail}\n"
                f"  The interactive calibrate gate runs inline on the next "
                f"`isa run --mode first-time` (without --dry-run): the backend drafts a "
                f"vocab, you review/lock it into config/tags.json, then enrich continues."
            )
        else:
            # agent-filled enrich gate
            print(
                f"\nNEXT (manual){label}: {na.group} — {na.detail}\n"
                f"  Run: isa run --stage enrich --prepare --group {na.group!r}\n"
                f"  Then fill tmp/enrich/prompt.txt → tmp/enrich/results.json, "
                f"and: isa run --stage enrich --apply"
            )
    else:
        # Should not normally reach here post-loop (no-progress guard fired)
        print(f"\nNext: {na.group} — {na.detail}{label}")


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "run":
        if args.stage is None:
            _dispatch_mode(args)
            return
        dispatch_run(args)
        return
    if args.command == "discover":
        env = _load_env()
        ig_username = args.ig_username or env.ig_username
        log_path = setup_logging("discover")
        print(f"Logging to {_rel(log_path)}")
        from insta_save.stages.discover import run_discover
        names = [args.collection] if args.collection else None
        summary = run_discover(env, ig_username=ig_username,
                               collections_path="config/collections.json",
                               tmp_dir=env.tmp_dir, headed=args.headed,
                               fresh=args.fresh, names=names)
        print(f"Discover: {len(summary['new'])} new, {len(summary['missing'])} missing, "
              f"index_complete={summary['index_complete']}, skipped={summary['skipped']}")
        return
    if args.command == "backup":
        env = _load_env()
        collections_cfg = _load_collections()
        log_path = setup_logging("backup")
        print(f"Logging to {_rel(log_path)}")
        out_dir = Path(env.tmp_dir) / "backups"
        if args.restore_check:
            # Find the newest backup file (ts format sorts lexically = chronologically).
            candidates = sorted(out_dir.glob("notion-*.json")) if out_dir.exists() else []
            if not candidates:
                print("Restore-check: no backup file found. Run `isa backup` first.")
                return
            newest = candidates[-1]
            result = restore_check(env, newest, collections_cfg)
            if result["ok"]:
                print(f"Restore-check: OK ({result['count']} pages match)")
            else:
                print(f"Restore-check: MISMATCH ({len(result['mismatches'])} issue(s), "
                      f"backup count={result['count']})")
                for m in result["mismatches"]:
                    print(f"  - {m}")
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = backup(env, out_dir=out_dir, ts=ts)
            # Re-read count from the written file so we don't need backup() to return it.
            import json as _json
            written_count = _json.loads(path.read_text(encoding="utf-8"))["count"]
            print(f"Backup: wrote {path} ({written_count} pages)")
        return
    if args.command == "status":
        env = _load_env()
        collections_cfg = _load_collections()
        setup_logging("status")
        if args.retry_failed:
            r = _retry_failed(env)
            print(f"Retry: requeued {r['requeued']} Failed items "
                  f"({r['to_extracted']}→Extracted, {r['to_queued']}→Queued).")
            return
        rows = build_status(env, collections_cfg)
        _print_status_table(rows)
        return
    raise SystemExit(f"isa {args.command}: not implemented yet (v2 scaffold — see ARCHITECTURE.md)")


if __name__ == "__main__":
    main()
