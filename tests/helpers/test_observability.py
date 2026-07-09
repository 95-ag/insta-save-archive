from insta_save.helpers import observability as obs


def test_render_rule_centers_label_at_width():
    line = obs.render_rule("discover", width=20)
    assert len(line) == 20 and "discover" in line
    assert line.startswith("─") and line.endswith("─")


def test_render_rule_indent_and_char():
    line = obs.render_rule("extract", width=12, char="═", indent=3)
    assert line.startswith("   ") and line[3] == "═" and len(line) == 15


def test_render_rule_index_suffix():
    assert "(1/6)" in obs.render_rule("Hustling", width=30, index=(1, 6))


def test_stage_section_prints_header_and_done(capsys):
    with obs.stage_section("run config", width=24):
        print("body")
    lines = [l for l in capsys.readouterr().out.splitlines() if l.strip()]
    assert "run config" in lines[0]
    assert "done" in lines[-1] and "run config" in lines[-1]


def test_stageprogress_header_uses_sized_rule(capsys):
    with obs.StageProgress("Ingest", width=30):
        pass
    head = [l for l in capsys.readouterr().out.splitlines() if "Ingest" in l and "done" not in l][0]
    assert len(head.rstrip()) <= 30 and "Ingest" in head


def test_stageprogress_nested_indents_done_rule(capsys):
    with obs.StageProgress("Extract", width=24, level=1):
        pass
    done = [l for l in capsys.readouterr().out.splitlines() if "done" in l and "Extract" in l][0]
    assert done.startswith("   ")


def test_flush_logs_flushes_all_root_handlers():
    import logging
    from insta_save.helpers import observability as obs
    flushed = []
    class _H(logging.Handler):
        def emit(self, record): pass
        def flush(self): flushed.append(True)
    root = logging.getLogger()
    h = _H()
    root.addHandler(h)
    try:
        obs.flush_logs()
        assert flushed == [True]
    finally:
        root.removeHandler(h)


import io
from rich.console import Console
from insta_save.helpers.observability import spinner


def test_spinner_disabled_is_a_silent_noop(capsys):
    with spinner("Working…", enabled=False):
        pass
    assert "Working" not in capsys.readouterr().out  # disabled -> renders nothing


def test_spinner_noops_when_not_a_tty():
    # Default stdout under pytest is not a terminal -> spinner must be a silent no-op.
    entered = False
    with spinner("Querying Notion…"):
        entered = True
    assert entered  # still yields, just doesn't render


def test_spinner_renders_when_console_is_a_terminal():
    # force_terminal makes is_terminal True -> the animate path runs without raising.
    buf = io.StringIO()
    con = Console(force_terminal=True, file=buf)
    entered = False
    with spinner("Asking Claude…", console=con):
        entered = True
    assert entered


def test_stageprogress_summary_interrupted_label_and_counters(capsys):
    # An exception inside the block frames the footer as "interrupted" (not "done")
    # and still reports the accumulated counters and elapsed line.
    try:
        with obs.StageProgress("Enrich", width=30) as p:
            p.bump("tagged", 3)
            p.bump("failed")
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    out = capsys.readouterr().out
    assert "interrupted" in out and "Enrich" in out
    assert "tagged=3" in out and "failed=1" in out
    assert "elapsed" in out


def test_retrywatcher_counts_matching_library_warnings_only():
    import logging

    calls = []
    watcher = obs._RetryWatcher(lambda: calls.append(1))

    def _record(name, msg):
        return logging.LogRecord(name, logging.WARNING, __file__, 0, msg, None, None)

    watcher.emit(_record("notion_client.client", "Request failed, retrying"))  # match
    watcher.emit(_record("httpx", "Read timed out"))                           # match
    watcher.emit(_record("myapp", "please retry"))          # ignored: not a library logger
    watcher.emit(_record("notion_client", "all good"))      # ignored: no retry/timeout token
    assert calls == [1, 1]
