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
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console, Group
from rich.live import Live
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.text import Text

RULE_TOP = 56
RULE_NESTED = 40
INDENT = 3


def render_rule(label, *, width, char="─", indent=0, index=None) -> str:
    """Return a fixed-width rule string with the label centered between rule chars.

    The returned string has exactly (indent + width) characters. indent pads the
    left with spaces; width governs the rule+label region. index, when given,
    appends a "(n/total)" suffix to the label before centering.
    """
    text = label if index is None else f"{label} ({index[0]}/{index[1]})"
    text = f" {text} "
    if len(text) >= width:
        return " " * indent + text[:width]
    pad = width - len(text)
    left = pad // 2
    right = pad - left
    return " " * indent + (char * left) + text + (char * right)


@contextmanager
def stage_section(label, *, width=RULE_TOP, char="─", indent=0, index=None, console=None):
    """Context manager that prints a framed header/footer around a block of output.

    Prints render_rule(label) on enter and render_rule("done · {label}") on exit.
    Uses a plain Console() by default so callers can substitute a custom one (e.g.
    a forced-width console in tests) via the console= kwarg.
    """
    con = console or Console()
    con.print(render_rule(label, width=width, char=char, indent=indent, index=index))
    try:
        yield
    finally:
        con.print(render_rule(f"done · {label}", width=width, char=char, indent=indent, index=index))


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

    # Keep noisy libraries in the file, never on screen. propagate=True lets their
    # records reach the file handler on root. notion_client's StreamHandler issue is
    # handled via the make_console_logger patch below.
    for name in _NOISY_LOGGERS:
        lg = logging.getLogger(name)
        for h in list(lg.handlers):
            lg.removeHandler(h)
        lg.addHandler(logging.NullHandler())
        lg.propagate = True
        lg.setLevel(logging.INFO)

    # notion_client.logging.make_console_logger unconditionally adds a new StreamHandler
    # on every Client() init (no "if not logger.handlers" guard), then Client.__init__
    # immediately calls setLevel(WARNING) — overwriting anything we set here.  Patching
    # make_console_logger to return the logger without attaching a handler is the only
    # reliable fix.  The NullHandler seeded above plus propagate=True keeps records
    # flowing to the file handler on root, and _RetryWatcher still sees them.
    try:
        import notion_client.logging as _nc_logging
        _nc_logging.make_console_logger = lambda: logging.getLogger("notion_client")
    except ImportError:
        pass

    return log_path


class _RetryWatcher(logging.Handler):
    """
    Logging handler that counts library retry/timeout WARNINGs (notion_client/httpx)
    and reports them to a callback — so retries show as a quiet in-place indicator
    instead of leaking warning spam to the terminal. Writes nothing itself.
    """

    _MATCH = ("fail", "timed out", "timeout", "retry")

    def __init__(self, on_retry):
        super().__init__(level=logging.WARNING)
        self._on_retry = on_retry

    def emit(self, record: logging.LogRecord) -> None:
        if not record.name.startswith(("notion_client", "httpx", "httpcore")):
            return
        msg = record.getMessage().lower()
        if any(token in msg for token in self._MATCH):
            try:
                self._on_retry()
            except Exception:
                pass


class StageProgress:
    """
    A reusable live terminal display built on rich.Progress + a live status line.

    Generic across stages — create one or more named bars, track arbitrary counters,
    and show the current item live. ETA and elapsed are automatic. A retry watcher
    surfaces Notion/httpx retries as a quiet in-place counter (full detail still goes
    to the log file via setup_logging) instead of leaking warning spam to the terminal.

    Not a logger: it owns the terminal exclusively while active.
    """

    def __init__(self, title: str, *, width: int = RULE_TOP, level: int = 0, char: str = "─"):
        self._title = title
        self._width = width
        self._indent = level * INDENT
        self._char = char
        self._console = Console()
        self._counters: dict[str, int] = {}
        self._current: str = ""
        self._retries: int = 0
        self._started = time.time()
        self._live: Live | None = None
        self._retry_handler: _RetryWatcher | None = None

        self._progress = Progress(
            SpinnerColumn(),
            TextColumn("[bold]{task.description}"),
            BarColumn(bar_width=max(8, width - 30)),
            MofNCompleteColumn(),
            TextColumn("•"),
            TimeElapsedColumn(),
            TextColumn("•  ETA"),
            TimeRemainingColumn(),
            console=self._console,
        )

    # -- live rendering -----------------------------------------------------

    def _status_line(self) -> Text:
        parts = []
        if self._current:
            parts.append(self._current)
        if self._retries:
            parts.append(f"↻ retries: {self._retries}")
        return Text("  " + "   ".join(parts), style="dim") if parts else Text("")

    def _renderable(self) -> Group:
        return Group(self._progress, self._status_line())

    def _refresh(self) -> None:
        if self._live is not None:
            self._live.update(self._renderable())

    def _on_retry(self) -> None:
        self._retries += 1
        self._refresh()

    # -- lifecycle ----------------------------------------------------------

    def __enter__(self) -> "StageProgress":
        self._console.print(render_rule(self._title, width=self._width, char=self._char, indent=self._indent))
        self._live = Live(self._renderable(), console=self._console, refresh_per_second=8)
        self._live.start()
        self._retry_handler = _RetryWatcher(self._on_retry)
        logging.getLogger().addHandler(self._retry_handler)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._retry_handler is not None:
            logging.getLogger().removeHandler(self._retry_handler)
        if self._live is not None:
            self._live.stop()
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
        """Show the current stage + item live on the status line under the bars."""
        self._current = f"{stage} · {item}"
        self._refresh()

    def log_line(self, message: str) -> None:
        """Print a one-off narrator line above the live display."""
        self._console.print(message)

    # -- summary ------------------------------------------------------------

    def _print_summary(self, failed: bool) -> None:
        elapsed = int(time.time() - self._started)
        h, rem = divmod(elapsed, 3600)
        m, s = divmod(rem, 60)

        counters = dict(self._counters)
        if self._retries:
            counters["notion_retries"] = self._retries
        line = "  ".join(f"{k}={v}" for k, v in counters.items())
        label = "interrupted" if failed else "done"
        pad = " " * self._indent
        self._console.print(render_rule(f"{label} · {self._title}", width=self._width, char=self._char, indent=self._indent))
        if line:
            self._console.print(f"{pad}  {line}")
        self._console.print(f"{pad}  elapsed {h}h {m}m {s}s")
