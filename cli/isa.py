"""isa — Insta-Save v2 CLI entrypoint. Arg surface only; stages are stubbed until built."""

import argparse
from pathlib import Path

from insta_save.config.env import load_env as _load_env
from insta_save.config.run import load_run_config as _load_run
from insta_save.config.collections import load_collections as _load_collections
from insta_save.config.tags import load_vocab
from insta_save.adapters.notion import ensure_schema
from insta_save.helpers.observability import StageProgress, setup_logging
from insta_save.stages.extract import run_extract_stage
from insta_save.stages import enrich
from insta_save.stages.calibrate import sample as calibrate_sample

STAGES = ["discover", "ingest", "select", "extract", "calibrate", "enrich", "deterministic", "route"]


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
    run.add_argument("--reenrich", action="store_true")
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

    sub.add_parser("status", help="Per-group counts: imported/extracted/tagged/failed/left.")
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
        print(f"Logging to {log_path}")
        with StageProgress("Extract") as progress:
            run_extract_stage(
                env, run_cfg.extract, progress,
                limit=args.limit, group=args.group,
                collections_cfg=collections_cfg, reextract=args.reextract)
        return
    if args.stage == "calibrate":
        # Backend-independent: calibrate samples + a session proposes vocab; it does NOT
        # read run_cfg.enrich.backend or call backend.fill (D18 human-reviewed gate).
        if not args.group:
            raise SystemExit("isa run --stage calibrate: --group is required")
        env = _load_env()
        log_path = setup_logging("calibrate")
        print(f"Logging to {log_path}")
        collections_cfg = _load_collections()
        statuses = ["Extracted"] + (["Summarized"] if args.reenrich else [])
        template = Path("prompts/calibrate_v2.0.txt").read_text(encoding="utf-8")
        with StageProgress("Calibrate") as progress:
            n = calibrate_sample(env, group=args.group, collections_cfg=collections_cfg,
                                 limit=args.calibrate_limit, statuses=statuses,
                                 prompt_template=template, progress=progress)
        if n == 0:
            print(f"No items to sample for group {args.group} (statuses: {', '.join(statuses)}). "
                  f"Add --reenrich to also sample already-Summarized items.")
        else:
            print(f"Sampled {n} items -> tmp/calibrate/prompt.txt. In a Claude session: "
                  f'"Read tmp/calibrate/prompt.txt and write tmp/calibrate/proposed_tags.json", '
                  f"then review + merge into config/tags.json.")
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

        statuses = ["Extracted"] + (["Summarized"] if args.reenrich else [])
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
            # prepare -> fill -> apply, looped until prepare batches nothing. --prepare/--apply
            # are ignored here (the loop does both). --group is required (prepare needs it).
            if not args.group:
                raise SystemExit("isa run --stage enrich: --group is required for the "
                                 f"{backend.NAME!r} backend (drains the group in one run)")
            vocab = load_vocab()
            collections_cfg = _load_collections()
            log_path = setup_logging("enrich")
            print(f"Logging to {log_path}")
            enrich_dir = Path(env.tmp_dir) / "enrich"
            while True:
                with StageProgress("Enrich prepare") as progress:
                    n = enrich.prepare(env, group=args.group, collections_cfg=collections_cfg,
                                       vocab=vocab, char_budget=budgets.char_budget,
                                       max_items=budgets.max_items, statuses=statuses,
                                       prompt_template=template, kinds=kinds,
                                       image_token_budget=image_budget,
                                       output_language=run_cfg.output_language, progress=progress)
                if n == 0:
                    print(f"ENRICH_DRAINED group={args.group} lane={args.lane}")
                    break
                backend.fill(env, run_cfg, enrich_dir)
                with StageProgress("Enrich apply") as progress:
                    counts = enrich.apply(env, vocab=vocab, model=run_cfg.enrich.model,
                                          progress=progress)
                print(f"Applied: {counts['written']} written, {counts['failed']} failed.")
                # No-progress guard: prepare batched items but apply wrote none (every item
                # failed, or fill produced no usable results). Those items stay Extracted, so
                # the next prepare re-selects them and the loop spins forever. Stop instead.
                if counts["written"] == 0:
                    print(f"enrich: no items applied for group {args.group} (lane={args.lane}) — "
                          f"stopping to avoid a no-progress loop. Check logs; resolve failures, "
                          f"then re-run.")
                    break
            return

        # agent-filled backends (claude-code/cowork): one prepare or apply step; the driving
        # session (or Cowork loop) runs the fill and re-invokes. --apply reads group from batch.json.
        if not args.apply and not args.group:
            raise SystemExit("isa run --stage enrich --prepare: --group is required")
        vocab = load_vocab()
        if args.apply:
            log_path = setup_logging("enrich-apply")
            print(f"Logging to {log_path}")
            with StageProgress("Enrich apply") as progress:
                counts = enrich.apply(env, vocab=vocab, model=run_cfg.enrich.model, progress=progress)
            print(f"Applied: {counts['written']} written, {counts['failed']} failed.")
            return
        # prepare (group guaranteed present by the guard above)
        log_path = setup_logging("enrich-prepare")
        print(f"Logging to {log_path}")
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
        print(f"Logging to {log_path}")
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
        print(f"Logging to {log_path}")
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
                print(f"Logging to {log_path}")
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
            if args.apply:
                log_path = setup_logging("deterministic-apply")
                print(f"Logging to {log_path}")
                with StageProgress("Deterministic apply") as progress:
                    counts = det.apply(env, progress=progress)
                print(f"Applied: {counts['written']} written, {counts['failed']} failed.")
                return
            if not args.group:
                raise SystemExit("isa run --stage deterministic --prepare: --group is required")
            log_path = setup_logging("deterministic-prepare")
            print(f"Logging to {log_path}")
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
        print(f"Logging to {log_path}")
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
        from insta_save.config.routes import load_routes
        from insta_save.stages.route import run_route_stage
        routes = load_routes()
        log_path = setup_logging("route")
        print(f"Logging to {log_path}")
        with StageProgress("Route") as progress:
            r = run_route_stage(env, routes, collections_cfg, progress,
                                limit=args.limit, group=args.group, dry_run=args.dry_run,
                                write_delay=env.notion_write_delay)
        print(f"Route: {r.get('routed', 0)} → Routed, {r.get('unrouted', 0)} left Tagged "
              f"(no mapping){' (dry-run)' if args.dry_run else ''}.")
        return

    raise SystemExit(f"isa run --stage {args.stage}: not implemented yet (v2 — see ARCHITECTURE.md)")


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "run":
        if args.stage is None:
            raise SystemExit("isa run: --stage is required for now (mode orchestration comes later)")
        dispatch_run(args)
        return
    if args.command == "discover":
        env = _load_env()
        ig_username = args.ig_username or env.ig_username
        log_path = setup_logging("discover")
        print(f"Logging to {log_path}")
        from insta_save.stages.discover import run_discover
        names = [args.collection] if args.collection else None
        summary = run_discover(env, ig_username=ig_username,
                               collections_path="config/collections.json",
                               tmp_dir=env.tmp_dir, headed=args.headed,
                               fresh=args.fresh, names=names)
        print(f"Discover: {len(summary['new'])} new, {len(summary['missing'])} missing, "
              f"index_complete={summary['index_complete']}, skipped={summary['skipped']}")
        return
    raise SystemExit(f"isa {args.command}: not implemented yet (v2 scaffold — see ARCHITECTURE.md)")


if __name__ == "__main__":
    main()
