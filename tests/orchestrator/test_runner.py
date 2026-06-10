from insta_save.orchestrator import runner
from insta_save.config.collections import CollectionsConfig


class _Progress:
    def __init__(self):
        self.counters = {}
        self.current = None
    def add_bar(self, label, total): return 0
    def advance(self, bar): pass
    def set_current(self, stage, item): self.current = item
    def bump(self, name, by=1): self.counters[name] = self.counters.get(name, 0) + by


def _fake_query(rows):
    def q(env, status, priority):
        return [r for r in rows if r["priority"] == priority]
    return q


def test_processes_high_to_low(monkeypatch):
    rows = [
        {"page_id": "a", "source_id": "A", "ig_link": "x", "type": "Reel",
         "collections": ["c1"], "priority": None},
        {"page_id": "b", "source_id": "B", "ig_link": "y", "type": "Reel",
         "collections": ["c1"], "priority": "High"},
    ]
    monkeypatch.setattr(runner, "query_by_status_and_priority", _fake_query(rows))
    order = []
    def process(env, item, ctx):
        order.append(item["source_id"])
        return "extracted"
    counts = runner.run_priority_stage(None, "Queued", process, _Progress(),
                                       stage_key="extract", bar_label="Extract")
    assert order == ["B", "A"]  # High before unprioritised
    assert counts["extracted"] == 2 and counts["failed"] == 0


def test_group_filter(monkeypatch):
    rows = [
        {"page_id": "a", "source_id": "A", "ig_link": "x", "type": "Reel",
         "collections": ["inHustling"], "priority": "High"},
        {"page_id": "b", "source_id": "B", "ig_link": "y", "type": "Reel",
         "collections": ["inBiz"], "priority": "High"},
    ]
    monkeypatch.setattr(runner, "query_by_status_and_priority", _fake_query(rows))
    cfg = CollectionsConfig(groups=("Hustling", "Biz", "uncategorized"),
                            collections={"inHustling": {"group": "Hustling", "extract": True},
                                         "inBiz": {"group": "Biz", "extract": True}})
    seen = []
    runner.run_priority_stage(None, "Queued", lambda e, i, c: seen.append(i["source_id"]) or "extracted",
                              _Progress(), stage_key="x", bar_label="X",
                              group="Hustling", collections_cfg=cfg)
    assert seen == ["A"]


def test_on_error_counts_failed(monkeypatch):
    rows = [{"page_id": "a", "source_id": "A", "ig_link": "x", "type": "Reel",
             "collections": [], "priority": "High"}]
    monkeypatch.setattr(runner, "query_by_status_and_priority", _fake_query(rows))
    handled = []
    def boom(env, item, ctx): raise ValueError("nope")
    counts = runner.run_priority_stage(None, "Queued", boom, _Progress(),
                                       on_error=lambda e, i, exc: handled.append(i["page_id"]),
                                       stage_key="x", bar_label="X")
    assert counts["failed"] == 1 and handled == ["a"]
