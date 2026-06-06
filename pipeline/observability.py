"""
Observability — reusable terminal progress and file logging for all pipeline stages.

Two output streams that never mix:
  1. Terminal  — a live rich display (progress bars, counters, current item). For humans.
  2. Log file  — full DEBUG detail (HTTP calls, per-item decisions). For debugging.

This module is stage-agnostic: ingest, extraction, and enrichment all use the same API.

Typical use:
    from pipeline.observability import setup_logging, StageProgress

    log_path = setup_logging("ingest")        # logs/ingest_<ts>.log; terminal stays clean
    with StageProgress("Ingest") as progress:
        bar = progress.add_bar("Collections", total=43)
        for col in collections:
            progress.set_current("crawl", col.name)
            ...
            progress.bump("created")
            progress.advance(bar)
    # summary prints automatically on exit
"""

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)

_LOGS_DIR = Path(__file__).parent.parent / "logs"

# Noisy third-party loggers — pinned to the file handler only, never the terminal.
_NOISY_LOGGERS = ("httpx", "httpcore", "notion_client", "urllib3", "asyncio")


def setup_logging(stage_name: str, level: int = logging.DEBUG) -> Path:
    """
    Route ALL logging to a timestamped file under logs/. Nothing goes to the
    terminal — the terminal is owned by StageProgress.

    Returns the path to the log file so the caller can surface it to the user.

    stage_name is a short slug like "ingest" / "extraction" / "enrichment".
    Timestamp is generated here (real wall clock) since logging is a side effect,
    not part of any resumable computation.
    """
    _LOGS_DIR.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    log_path = _LOGS_DIR / f"{stage_name}_{ts}.log"

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )

    root = logging.getLogger()
    root.setLevel(level)
    # Remove any pre-existing handlers (e.g. a StreamHandler from basicConfig)
    # so log lines never leak onto the terminal and break the live display.
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(file_handler)

    # Keep noisy libraries in the file, never on screen.
    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.INFO)

    return log_path


class StageProgress:
    """
    A reusable live terminal display built on rich.Progress.

    Generic across stages — create one or more named bars, track arbitrary
    counters, and show the current item. ETA and elapsed time are automatic.

    Not a logger: detail goes to the log file via setup_logging(). This class
    owns the terminal exclusively while active.
    """

    def __init__(self, title: str):
        self._title = title
        self._console = Console()
        self._counters: dict[str, int] = {}
        self._current: str = ""
        self._started = time.time()

        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•  ETA"),
            TimeRemainingColumn(),
            console=self._console,
            transient=False,
        )

    # -- lifecycle ----------------------------------------------------------

    def __enter__(self) -> "StageProgress":
        self._console.rule(f"[bold]{self._title}")
        self._progress.start()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._progress.stop()
        self._print_summary(failed=exc_type is not None)

    # -- bars ---------------------------------------------------------------

    def add_bar(self, description: str, total: int | None) -> int:
        """Add a progress bar. Returns a task id to pass to advance()/update_total()."""
        return self._progress.add_task(description, total=total)

    def advance(self, task_id: int, step: int = 1) -> None:
        self._progress.advance(task_id, step)

    def update_total(self, task_id: int, total: int) -> None:
        """Set a bar's total once it becomes known (e.g. post count after crawl)."""
        self._progress.update(task_id, total=total)

    def reset_bar(self, task_id: int, total: int, description: str | None = None) -> None:
        """Reset a bar to 0/total — e.g. the inner 'Posts' bar per collection."""
        kwargs = {"completed": 0, "total": total}
        if description is not None:
            kwargs["description"] = description
        self._progress.update(task_id, **kwargs)

    # -- counters + current item -------------------------------------------

    def bump(self, name: str, by: int = 1) -> None:
        """Increment a named counter (e.g. 'created', 'updated', 'skipped', 'failed')."""
        self._counters[name] = self._counters.get(name, 0) + by

    def set_current(self, stage: str, item: str) -> None:
        """Record the current stage + item (shown in the summary; logged in detail)."""
        self._current = f"{stage} · {item}"

    def log_line(self, message: str) -> None:
        """Print a one-off narrator line above the bars without disturbing them."""
        self._console.print(message)

    # -- summary ------------------------------------------------------------

    def _print_summary(self, failed: bool) -> None:
        elapsed = int(time.time() - self._started)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)

        line = "  ".join(f"{k}={v}" for k, v in self._counters.items())
        status = "[red]INTERRUPTED[/red]" if failed else "[green]DONE[/green]"
        self._console.rule(f"{status} · {self._title}")
        if line:
            self._console.print(f"  {line}")
        self._console.print(f"  elapsed {h}h {m}m {s}s")
