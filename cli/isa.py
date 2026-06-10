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

    sub.add_parser("discover", help="Surface collections + configure / diff.")

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
        if not args.group:
            raise SystemExit("isa run --stage calibrate: --group is required")
        env = _load_env()
        log_path = setup_logging("calibrate")
        print(f"Logging to {log_path}")
        collections_cfg = _load_collections()
        statuses = ["Extracted"] + (["Summarized"] if args.reenrich else [])
        template = Path("prompts/calibrate_v2.0.txt").read_text(encoding="utf-8")
        n = calibrate_sample(env, group=args.group, collections_cfg=collections_cfg,
                             limit=args.calibrate_limit, statuses=statuses,
                             prompt_template=template)
        if n == 0:
            print(f"No items to sample for group {args.group} (statuses: {', '.join(statuses)}). "
                  f"Add --reenrich to also sample already-Summarized items.")
        else:
            print(f"Sampled {n} items -> tmp/calibrate/prompt.txt. In a Claude session: "
                  f'"Read tmp/calibrate/prompt.txt and write tmp/calibrate/proposed_tags.json", '
                  f"then review + merge into config/tags.json.")
        return

    if args.stage == "enrich":
        # validate args BEFORE loading config/vocab (fail fast; --apply reads the group
        # from batch.json, so it needs no --group). load_vocab() must not run for a
        # bad-args prepare — config/tags.json may not exist until calibrate has locked it.
        if not args.apply and not args.group:
            raise SystemExit("isa run --stage enrich --prepare: --group is required")
        env = _load_env()
        run_cfg = _load_run()
        vocab = load_vocab()
        if args.apply:
            log_path = setup_logging("enrich-apply")
            print(f"Logging to {log_path}")
            counts = enrich.apply(env, vocab=vocab, model=run_cfg.enrich.model)
            print(f"Applied: {counts['written']} written, {counts['failed']} failed.")
            return
        # prepare (group guaranteed present by the guard above)
        log_path = setup_logging("enrich-prepare")
        print(f"Logging to {log_path}")
        collections_cfg = _load_collections()
        statuses = ["Extracted"] + (["Summarized"] if args.reenrich else [])
        template = Path("prompts/enrich_v2.0.txt").read_text(encoding="utf-8")
        n = enrich.prepare(env, group=args.group, collections_cfg=collections_cfg, vocab=vocab,
                           char_budget=run_cfg.char_budget, max_items=run_cfg.max_items,
                           statuses=statuses, prompt_template=template)
        if n == 0:
            print(f"No items left to enrich in group {args.group}.")
        else:
            print(f"Prepared {n} items -> tmp/enrich/prompt.txt. In a Claude session: "
                  f'"Read tmp/enrich/prompt.txt and write tmp/enrich/results.json", '
                  f"then: isa run --stage enrich --apply")
        return

    raise SystemExit(f"isa run --stage {args.stage}: not implemented yet (v2 — see ARCHITECTURE.md)")


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "run":
        if args.stage is None:
            raise SystemExit("isa run: --stage is required for now (mode orchestration comes later)")
        dispatch_run(args)
        return
    raise SystemExit(f"isa {args.command}: not implemented yet (v2 scaffold — see ARCHITECTURE.md)")


if __name__ == "__main__":
    main()
