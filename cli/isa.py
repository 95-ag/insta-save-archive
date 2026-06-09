"""isa — Insta-Save v2 CLI entrypoint. Arg surface only; stages are stubbed until built."""

import argparse

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


def main() -> None:
    args = build_parser().parse_args()
    raise SystemExit(f"isa {args.command}: not implemented yet (v2 scaffold — see ARCHITECTURE.md)")


if __name__ == "__main__":
    main()
