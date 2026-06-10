"""isa — Insta-Save v2 CLI entrypoint. Arg surface only; stages are stubbed until built."""

import argparse

from insta_save.config.env import load_env as _load_env
from insta_save.config.run import load_run_config as _load_run
from insta_save.config.collections import load_collections as _load_collections
from insta_save.adapters.notion import ensure_schema
from insta_save.observability import StageProgress, setup_logging
from insta_save.stages.extract import run_extract_stage

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
