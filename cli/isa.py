"""isa — Insta-Save v2 CLI entrypoint. Arg surface only; stages are stubbed until built."""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

from insta_save.config.env import load_env as _load_env
from insta_save.config.run import load_run_config as _load_run
from insta_save.config.collections import load_collections as _load_collections
from insta_save.config.tags import load_vocab, load_vocab_or_empty
from insta_save.adapters.notion import ensure_schema
from insta_save.helpers import tui
from insta_save.helpers.observability import StageProgress, setup_logging
from insta_save.stages.extract import run_extract_stage
from insta_save.stages import enrich
from insta_save.orchestrator.calibrate_gate import run_calibrate_gate
from insta_save.backup import backup, restore_check
from insta_save.backends.base import get_backend, TerminalBackendError
from insta_save.config.routes import load_routes
from insta_save.orchestrator import guardrails
from insta_save.orchestrator.preflight import preflight
from insta_save.orchestrator.status_report import build_status, retry_failed as _retry_failed
from insta_save.orchestrator.config_gate import ensure_run_json, run_config_gate

STAGES = ["discover", "ingest", "select", "extract", "calibrate", "enrich", "deterministic",
          "route", "distribute"]


def _guard(load, *args, **kwargs):
    """Call a config loader, converting its known errors into a clean, actionable SystemExit
    instead of a raw traceback. Used on the secondary commands / --stage handlers (the
    `isa run` path has its own targeted handling)."""
    try:
        return load(*args, **kwargs)
    except (RuntimeError, FileNotFoundError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise SystemExit(str(exc))


def _rel(p: str) -> str:
    """Return a relative form of an absolute log path for readable console output."""
    return os.path.relpath(str(p))

def _distribute_label(source_id, title) -> str:
    """Display text for an item preview line: the title, or the source_id when there is
    none (never a placeholder — see data-integrity.md)."""
    return title if title is not None else source_id


def _format_distribute_preview(preview) -> str:
    """Render an ApplyPreview compactly: one line per section change, then the item lists
    a reviewer needs to check by hand (dropped, unmentioned, unverified). Folded-and-linked
    items are only counted — the diff already shows what changed for them."""
    lines = []
    if preview.sections:
        for section in preview.sections:
            marker = "+ new" if section.action == "append" else "~ replace"
            lines.append(f"  {marker} {section.heading!r} (+{section.added} -{section.removed})")
    else:
        lines.append("  No page content changes")

    lines.append(f"  folded: {len(preview.folded)}")
    if preview.dropped:
        lines.append(f"  dropped ({len(preview.dropped)}):")
        for source_id, title, reason in preview.dropped:
            suffix = f" — {reason}" if reason else ""
            lines.append(f"    - {_distribute_label(source_id, title)}{suffix}")
    if preview.unmentioned:
        lines.append(
            f"  not mentioned, will be marked Distributed as a duplicate skip "
            f"({len(preview.unmentioned)}):"
        )
        for source_id, title in preview.unmentioned:
            lines.append(f"    - {_distribute_label(source_id, title)}")
    if preview.unverified:
        lines.append(
            f"  folded but the link is missing from the page, will stay Routed "
            f"({len(preview.unverified)}):"
        )
        for source_id, title in preview.unverified:
            lines.append(f"    - {_distribute_label(source_id, title)}")
    return "\n".join(lines)


def _format_rebuild_preview(preview) -> str:
    """Render a RebuildPreview compactly: per-section block counts, items grouped by what
    rebuild will do to them (reset to Tagged, or unlink only), and any refused sections a
    reviewer must fix in Notion before rebuild will touch the page."""
    lines = []
    if preview.sections:
        for heading, count in preview.sections:
            lines.append(f"  section {heading!r}: {count} block(s) to delete")
    else:
        lines.append("  No sections")
    lines.append(f"  blocks kept (dividers/empty paragraphs): {preview.blocks_kept}")
    if preview.heading_needs_clearing:
        lines.append("  content anchor heading still has text — will be cleared")

    relations_only_ids = {source_id for source_id, _, _ in preview.relations_only}
    reset_items = [row for row in preview.items if row[0] not in relations_only_ids]
    if reset_items:
        by_status: dict = {}
        for source_id, title, status in reset_items:
            by_status.setdefault(status, []).append((source_id, title))
        lines.append(f"  items to reset to Tagged ({len(reset_items)}):")
        for status in sorted(by_status):
            lines.append(f"    {status}:")
            for source_id, title in by_status[status]:
                lines.append(f"      - {_distribute_label(source_id, title)}")

    if preview.relations_only:
        lines.append(f"  items to unlink only, status untouched ({len(preview.relations_only)}):")
        for source_id, title, status in preview.relations_only:
            lines.append(f"    - {_distribute_label(source_id, title)} ({status})")

    if preview.refused:
        lines.append(f"  refused sections ({len(preview.refused)}) — fix these in Notion first:")
        for heading, kinds in preview.refused:
            lines.append(f"    - {heading!r}: {', '.join(kinds)}")

    return "\n".join(lines)


def _format_reroute_summary(r: dict, dry_run: bool) -> str:
    """Render a run_reroute_stage result: the headline counts, how the moved items' new
    targets were decided, then one line per old -> new move so a reviewer can confirm only
    the intended items shifted."""
    headline = (
        f"Reroute: {r.get('rerouted', 0)} rerouted, {r.get('unrouted', 0)} back to Tagged "
        f"(no route), {r.get('unchanged', 0)} unchanged"
    )
    if r.get("skipped_stale", 0):
        headline += f", {r['skipped_stale']} skipped (stale)"
    if r.get("failed", 0):
        headline += f", {r['failed']} failed"
    headline += f"{' (dry-run)' if dry_run else ''}."
    decided = r.get("decided_by", {})
    lines = [headline,
             f"  decided by primary topic: {decided.get('primary', 0)}, "
             f"by rule order: {decided.get('order', 0)}"]
    moves = r.get("moves", {})
    for (old, new), count in sorted(moves.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "")):
        lines.append(f"  {old or '(none)'} → {new or 'Tagged'} : {count}")
    return "\n".join(lines)


_STATUS_COLS = ("Imported", "Queued", "Extracted", "Tagged", "Routed", "Distributed", "Misrouted",
                "Failed", "remaining")


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
    run.add_argument("--collection", default=None)
    run.add_argument("--fresh", action="store_true")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--headed", action="store_true")
    run.add_argument("--confirm-removed", action="append", default=None)
    run.add_argument("--lane", choices=["text", "vision"], default="text")
    run.add_argument("--status", action="store_true")
    run.add_argument("--select-mode", choices=["inline", "editor"], default="inline")
    run.add_argument("--target", default=None,
                     help="distribute: the route_target page name to ingest into")
    run.add_argument("--rebuild", action="store_true",
                     help="distribute: reset --target to its template and its items to Tagged")
    run.add_argument("--reroute", action="store_true",
                     help="route: re-evaluate already-Routed items against the current routes.json")
    run.add_argument("--yes", action="store_true",
                     help="skip the distribute apply/rebuild confirmation")

    st = sub.add_parser("status", help="Per-group counts: imported/extracted/tagged/failed/left.")
    st.add_argument("--retry-failed", action="store_true",
                    help="Requeue all Failed items back to their inferred prior status.")
    bk = sub.add_parser("backup", help="Snapshot Notion to JSON.")
    bk.add_argument("--restore-check", action="store_true")
    return p


def dispatch_run(args) -> None:
    if args.stage == "extract":
        env = _guard(_load_env)
        run_cfg = _guard(_load_run)
        collections_cfg = _guard(_load_collections)
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
        env = _guard(_load_env)
        run_cfg = _guard(_load_run)
        collections_cfg = _guard(_load_collections)
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
        env = _guard(_load_env)
        run_cfg = _guard(_load_run)
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
            collections_cfg = _guard(_load_collections)
            print(f"{args.group}: {cowork.status(env, collections_cfg, args.group)} "
                  f"enrichable remaining")
            return

        statuses = ["Extracted"]
        if args.lane == "vision":
            kinds = {"Carousel", "Post"}
            template = Path("prompts/enrich_vision_v2.1.txt").read_text(encoding="utf-8")
            image_budget = budgets.image_token_budget
        else:
            kinds = {"Reel", "IGTV"}
            template = Path("prompts/enrich_v2.1.txt").read_text(encoding="utf-8")
            image_budget = None

        if backend.AUTOMATED:
            # local/api fill results.json in-process, so one invocation DRAINS the group:
            # drain_enrich_group loops prepare→fill→apply per lane until drained.
            # --prepare/--apply are ignored here (drain handles both). --group is required.
            if not args.group:
                raise SystemExit("isa run --stage enrich: --group is required for the "
                                 f"{backend.NAME!r} backend (drains the group in one run)")
            vocab = _guard(load_vocab)
            collections_cfg = _guard(_load_collections)
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
        vocab = _guard(load_vocab)
        if args.apply:
            log_path = setup_logging("enrich-apply")
            print(f"Logging to {_rel(log_path)}")
            collections_cfg = _guard(_load_collections)
            with StageProgress("Enrich apply") as progress:
                try:
                    counts = enrich.apply(env, vocab=vocab, model=run_cfg.enrich.model,
                                          collections_cfg=collections_cfg, progress=progress)
                except FileNotFoundError as exc:
                    raise SystemExit(str(exc))
            print(f"Applied: {counts['written']} written, {counts['failed']} failed.")
            return
        # prepare (group guaranteed present by the guard above)
        log_path = setup_logging("enrich-prepare")
        print(f"Logging to {_rel(log_path)}")
        collections_cfg = _guard(_load_collections)
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
        env = _guard(_load_env)
        collections_cfg = _guard(_load_collections)
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
        env = _guard(_load_env)
        collections_cfg = _guard(_load_collections)
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
        env = _guard(_load_env)
        run_cfg = _guard(_load_run)
        collections_cfg = _guard(_load_collections)
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
                        try:
                            counts = det.apply(env, progress=progress)
                        except FileNotFoundError as exc:
                            raise SystemExit(str(exc))
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
                    try:
                        counts = det.apply(env, progress=progress)
                    except FileNotFoundError as exc:
                        raise SystemExit(str(exc))
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
        env = _guard(_load_env)
        collections_cfg = _guard(_load_collections)
        ensure_schema(env)
        from insta_save.stages.route import run_route_stage, run_reroute_stage
        routes = load_routes()
        log_path = setup_logging("route")
        print(f"Logging to {_rel(log_path)}")
        if args.reroute:
            with StageProgress("Reroute") as progress:
                r = run_reroute_stage(env, routes, collections_cfg, progress,
                                      limit=args.limit, group=args.group, dry_run=args.dry_run,
                                      write_delay=0 if args.dry_run else env.notion_write_delay)
            print(_format_reroute_summary(r, args.dry_run))
            return
        with StageProgress("Route") as progress:
            r = run_route_stage(env, routes, collections_cfg, progress,
                                limit=args.limit, group=args.group, dry_run=args.dry_run,
                                write_delay=0 if args.dry_run else env.notion_write_delay)
        print(f"Route: {r.get('routed', 0)} → Routed, {r.get('unrouted', 0)} left Tagged "
              f"(no mapping){' (dry-run)' if args.dry_run else ''}.")
        return

    if args.stage == "distribute":
        from insta_save.backends import base
        from insta_save.helpers.observability import flush_logs
        from insta_save.stages import distribute

        # Validate before loading anything — a bad invocation must not touch real config.
        if args.rebuild and args.apply:
            raise SystemExit(
                "isa run --stage distribute: --rebuild and --apply cannot be combined"
            )
        if not args.target:
            raise SystemExit("isa run --stage distribute: --target '<page name>' is required")
        env = _guard(_load_env)
        ensure_schema(env)
        run_cfg = _guard(_load_run)
        version = f"{run_cfg.distribute.model}/{env.distribute_version}"
        log_path = setup_logging("distribute")
        print(f"Logging to {_rel(log_path)}")

        try:
            if args.rebuild:
                review = distribute.rebuild_preview(env, target=args.target)
                print(_format_rebuild_preview(review))

                if review.refused:
                    problems = "; ".join(distribute.refusal_messages(review.refused))
                    raise SystemExit(
                        "isa run --stage distribute --rebuild: refusing — fix these sections "
                        f"in Notion first: {problems}"
                    )

                if (not review.items and review.blocks_to_delete == 0
                        and not review.heading_needs_clearing):
                    print(f"Nothing to rebuild for {args.target!r}.")
                    return

                if not review.items and review.blocks_to_delete:
                    print("⚠ No archive items are linked to this page — its content may be "
                          "hand-built. Rebuilding deletes it.")

                if args.yes:
                    proceed = True
                elif not sys.stdin.isatty():
                    raise SystemExit(
                        "isa run --stage distribute --rebuild: refusing to change Notion "
                        "without confirmation in a non-interactive run — re-run in a "
                        "terminal or pass --yes"
                    )
                else:
                    choice = tui.confirm_action(
                        f"Rebuild {args.target!r}? This removes its distributed content and "
                        "resets its items.",
                        [
                            ("Rebuild this page", "rebuild",
                             "delete the page's content and reset its items to Tagged"),
                            ("Cancel — change nothing", "cancel", "write nothing"),
                        ],
                    )
                    proceed = choice == "rebuild"

                if not proceed:
                    print("Cancelled — nothing changed.")
                    return

                counts = distribute.rebuild(env, target=args.target)
                print(f"Rebuilt {args.target!r}: {counts['items_reset']} item(s) reset to "
                      f"Tagged, {counts['relations_removed']} relation(s) removed, "
                      f"{counts['relations_only']} item(s) unlinked only, "
                      f"{counts['blocks_deleted']} block(s) deleted, heading "
                      f"{'cleared' if counts['heading_cleared'] else 'left as is'}.")
                if counts.get("page_skipped"):
                    print(f"  {args.target!r} has no content heading — only its items "
                          "were reset.")
                print("  Re-running after a partial failure is safe: already-reset items "
                      "show as unlink-only next time.")
                return

            if args.apply:
                review = distribute.preview(env, target=args.target)
                print(_format_distribute_preview(review))

                if args.yes:
                    proceed = True
                elif not sys.stdin.isatty():
                    raise SystemExit(
                        "isa run --stage distribute --apply: refusing to write without "
                        "confirmation in a non-interactive run — re-run in a terminal or "
                        "pass --yes"
                    )
                else:
                    proceed = False
                    while True:
                        choice = tui.confirm_action(
                            f"Apply these changes to {args.target!r}?",
                            [
                                ("Apply these changes", "apply",
                                 "write the section and item changes to the page"),
                                ("Show the full diff", "diff",
                                 "print the unified diff, then ask again"),
                                ("Cancel — write nothing", "cancel",
                                 "keep the staged files, write nothing"),
                            ],
                        )
                        if choice == "diff":
                            print(review.diff_text)
                            continue
                        proceed = choice == "apply"
                        break

                if not proceed:
                    d = distribute.staging_dir(env, args.target)
                    print(f"Cancelled — nothing written; staged files kept in {_rel(d)}.")
                    return

                counts = distribute.apply(env, target=args.target, version=version)
                print(f"Distributed: {counts['folded']} folded in, {counts['skipped']} already "
                      f"covered, {counts['misrouted']} misrouted.")
                if counts.get("unverified", 0):
                    print(f"  {counts['unverified']} folded item(s) had no link in the applied "
                          f"page and were left Routed — see the log.")
                return

            routes = load_routes()
            vocab = _guard(load_vocab)
            budgets = base.get_backend(run_cfg.distribute.backend).batch_budgets(run_cfg)
            n = distribute.prepare(env, target=args.target, routes=routes, vocab=vocab,
                                   budgets=budgets, version=version)
            if n == 0:
                print(f"Nothing left to distribute for {args.target!r}.")
                return
            result = distribute.fill(
                env, target=args.target, routes=routes, vocab=vocab, budgets=budgets,
                model=run_cfg.distribute.model,
                template=Path("prompts/distribute_v2.1.txt").read_text(encoding="utf-8"))
            d = distribute.staging_dir(env, args.target)
            print(_format_distribute_preview(distribute.preview(env, target=args.target)))
            print(f"Staged {n} items in {result['calls']} call(s) -> {_rel(d)}\n"
                  f"  Review {_rel(d / 'diff.txt')}, edit {_rel(d / 'proposed.md')} if needed, then:\n"
                  f"  isa run --stage distribute --target {args.target!r} --apply")
            print("Nothing has been written to the page yet.")
            return
        except TerminalBackendError as exc:
            flush_logs()
            print(f"\n⏹  stopped · {exc}")
            print("   This won't resolve by retrying now (usage limit / auth). "
                  f"Resume later with: isa run --stage distribute --target {args.target!r}"
                  f"{' --apply' if args.apply else ''}")
            return
        except (RuntimeError, ValueError) as exc:
            raise SystemExit(str(exc))

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

    vocab = _guard(load_vocab_or_empty)
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
    from insta_save.orchestrator import run_control
    from insta_save.helpers.observability import flush_logs
    ig_username = env.ig_username or os.environ.get("IG_USERNAME", "")
    try:
        with run_control.RunControl(mode=args.mode):
            plan = run_pipeline(env, run_cfg, collections_cfg, vocab, backend, routes,
                                mode=args.mode, dry_run=args.dry_run,
                                select_mode=getattr(args, "select_mode", "inline"),
                                ig_username=ig_username, headed=args.headed,
                                fresh=args.fresh, progress_factory=_nested_progress)
    except TerminalBackendError as exc:
        flush_logs()
        print(f"\n⏹  stopped · {exc}")
        print(f"   This won't resolve by retrying now (usage limit / auth). "
              f"Resume later with: isa run --mode {args.mode}")
        return
    except (run_control.RunStopped, KeyboardInterrupt):
        flush_logs()
        print(f"⏹  stopped · resume with: isa run --mode {args.mode}")
        return
    _print_plan(plan, args.dry_run)


def _print_plan(plan, dry_run: bool) -> None:
    """Print the returned plan: one line per GroupStep, then a gate or done message."""
    label = " (dry-run)" if dry_run else ""
    for step in plan.steps:
        print(f"  {step.group}: {step.action} — {step.detail}")

    skipped = getattr(plan, "skipped", [])
    if skipped:
        print("\n⚠  skipped (no forward progress — re-run to retry, "
              "or `isa status --retry-failed` for items marked Failed):")
        for s in skipped:
            print(f"  - {s['group']} ({s['action']}): {s['reason']}")

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


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        if args.stage is None:
            _dispatch_mode(args)
            return
        dispatch_run(args)
        return
    if args.command == "discover":
        env = _guard(_load_env)
        ig_username = args.ig_username or env.ig_username
        log_path = setup_logging("discover")
        print(f"Logging to {_rel(log_path)}")
        from insta_save.stages.discover import run_discover
        names = [args.collection] if args.collection else None
        try:
            summary = run_discover(env, ig_username=ig_username,
                                   collections_path="config/collections.json",
                                   tmp_dir=env.tmp_dir, headed=args.headed,
                                   fresh=args.fresh, names=names)
        except RuntimeError as exc:
            raise SystemExit(str(exc))
        print(f"Discover: {len(summary['new'])} new, {len(summary['missing'])} missing, "
              f"index_complete={summary['index_complete']}, skipped={summary['skipped']}")
        return
    if args.command == "backup":
        env = _guard(_load_env)
        collections_cfg = _guard(_load_collections)
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
            written_count = json.loads(path.read_text(encoding="utf-8"))["count"]
            print(f"Backup: wrote {path} ({written_count} pages)")
        return
    if args.command == "status":
        env = _guard(_load_env)
        collections_cfg = _guard(_load_collections)
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
