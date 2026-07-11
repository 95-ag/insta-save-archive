# tests/stages/test_calibrate.py
import json
import types
from insta_save.stages import calibrate


class _FakeCols:
    """Config-accurate stand-in for CollectionsConfig. spec: {collection: (group, extract)}."""

    def __init__(self, spec):
        self._spec = spec
        self._groups = []
        for _, (g, _e) in spec.items():
            if g not in self._groups:
                self._groups.append(g)

    def group_of(self, c):
        entry = self._spec.get(c)
        return entry[0] if entry else None

    def collections_in_group(self, g):
        return {n for n, (grp, _e) in self._spec.items() if grp == g}

    def extract_collections_in_group(self, g):
        return {n for n, (grp, e) in self._spec.items() if grp == g and e}

    def extract_groups_of(self, names):
        return [g for g in self._groups
                if any((self._spec.get(n) or (None, False))[0] == g
                       and (self._spec.get(n) or (None, False))[1] for n in names)]


# hust-a + Side Projects are both extract=yes in Hustling.
_HUST = _FakeCols({"hust-a": ("Hustling", True), "Side Projects": ("Hustling", True)})
# big + small are both extract=yes in group G.
_G = _FakeCols({"big": ("G", True), "small": ("G", True)})


def _env(tmp_path):
    return types.SimpleNamespace(tmp_dir=str(tmp_path))


def test_sample_collects_group_items_and_writes_prompt(tmp_path, monkeypatch):
    stubs = [{"page_id": "p1", "source_id": "s1", "collections": ["hust-a"]},
            {"page_id": "p2", "source_id": "s2", "collections": ["other"]}]
    monkeypatch.setattr(calibrate, "query_by_status", lambda env, status: stubs)
    monkeypatch.setattr(calibrate, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": "s1", "caption": "c",
                                          "transcript": "t", "ocr_text": "", "type": "Reel"})

    n = calibrate.sample(_env(tmp_path), group="Hustling", collections_cfg=_HUST,
                         limit=20, statuses=["Extracted"], prompt_template="CAL {group}")
    assert n == 1   # only the hust-a item
    sample = json.loads((tmp_path / "calibrate" / "sample.json").read_text())
    assert [i["page_id"] for i in sample["items"]] == ["p1"]
    prompt = (tmp_path / "calibrate" / "prompt.txt").read_text()
    assert "Hustling" in prompt and "p1" in prompt


def test_sample_respects_limit(tmp_path, monkeypatch):
    stubs = [{"page_id": f"p{i}", "source_id": f"s{i}", "collections": ["hust-a"]}
            for i in range(5)]
    monkeypatch.setattr(calibrate, "query_by_status", lambda env, status: stubs)
    monkeypatch.setattr(calibrate, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": pid, "caption": "c",
                                          "transcript": "", "ocr_text": "", "type": "Reel"})
    n = calibrate.sample(_env(tmp_path), group="Hustling", collections_cfg=_HUST,
                         limit=3, statuses=["Extracted"], prompt_template="CAL {group}")
    assert n == 3


def test_sample_round_robins_across_collections(tmp_path, monkeypatch):
    stubs = [{"page_id": f"b{i}", "source_id": f"b{i}", "collections": ["big"]} for i in range(5)] \
           + [{"page_id": "s0", "source_id": "s0", "collections": ["small"]}]
    monkeypatch.setattr(calibrate, "query_by_status", lambda env, s: stubs)
    monkeypatch.setattr(calibrate, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": pid, "type": "Reel"})
    calibrate.sample(_env(tmp_path), group="G", collections_cfg=_G,
                     limit=3, statuses=["Extracted"], prompt_template="{group}")
    pids = [i["page_id"] for i in json.loads((tmp_path / "calibrate" / "sample.json").read_text())["items"]]
    assert "s0" in pids and len(pids) == 3      # small collection represented despite big's 5


def test_sample_size_adapts_to_collection_size(tmp_path, monkeypatch):
    # big=40 -> ceil(40*0.25)=10 (capped at _MAX_PER_COLL=10); small=2 -> min(2, max(0.5→3))=2 ; total 12
    big = [{"page_id": f"b{i}", "source_id": f"b{i}", "collections": ["big"]} for i in range(40)]
    small = [{"page_id": f"s{i}", "source_id": f"s{i}", "collections": ["small"]} for i in range(2)]
    monkeypatch.setattr(calibrate, "query_by_status", lambda env, s: big + small)
    monkeypatch.setattr(calibrate, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": pid, "type": "Reel"})
    n = calibrate.sample(_env(tmp_path), group="G", collections_cfg=_G,
                         limit=None, statuses=["Extracted"], prompt_template="{group}")
    pids = [i["page_id"] for i in json.loads((tmp_path / "calibrate" / "sample.json").read_text())["items"]]
    assert n == 12
    assert len([p for p in pids if p.startswith("b")]) == 10
    assert len([p for p in pids if p.startswith("s")]) == 2


def test_prompt_includes_only_extract_yes_collection_names(tmp_path, monkeypatch):
    # Guidance labels are the group's extract=yes collections — a det collection in the
    # same group is NOT surfaced as guidance (its items don't enrich under this group).
    spec = _FakeCols({"hust-a": ("Hustling", True), "hust-det": ("Hustling", False)})
    stubs = [{"page_id": "p1", "source_id": "s1", "collections": ["hust-a"]}]
    monkeypatch.setattr(calibrate, "query_by_status", lambda env, s: stubs)
    monkeypatch.setattr(calibrate, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": "s1", "caption": "c", "type": "Reel"})
    calibrate.sample(_env(tmp_path), group="Hustling", collections_cfg=spec,
                     limit=20, statuses=["Extracted"], prompt_template="CAL {group} COLS {collections}")
    prompt = (tmp_path / "calibrate" / "prompt.txt").read_text()
    assert "hust-a" in prompt and "hust-det" not in prompt


def test_sample_excludes_cross_group_det_only_membership(tmp_path, monkeypatch):
    # An item in G's DET collection plus ANOTHER group's extract=yes collection enriches
    # under the other group — so it must be excluded from G's sample and included in Other's.
    spec = _FakeCols({"g-ex": ("G", True), "g-det": ("G", False), "o-ex": ("Other", True)})
    stubs = [
        {"page_id": "native", "source_id": "native", "collections": ["g-ex"]},
        {"page_id": "cross", "source_id": "cross", "collections": ["g-det", "o-ex"]},
    ]
    monkeypatch.setattr(calibrate, "query_by_status", lambda env, s: stubs)
    monkeypatch.setattr(calibrate, "get_page_content",
                        lambda env, pid: {"page_id": pid, "source_id": pid, "type": "Reel"})

    n_g = calibrate.sample(_env(tmp_path), group="G", collections_cfg=spec,
                           limit=None, statuses=["Extracted"], prompt_template="{group} {collections}")
    g_pids = [i["page_id"] for i in json.loads((tmp_path / "calibrate" / "sample.json").read_text())["items"]]
    assert n_g == 1 and g_pids == ["native"]
    g_prompt = (tmp_path / "calibrate" / "prompt.txt").read_text()
    assert "g-ex" in g_prompt and "g-det" not in g_prompt

    n_o = calibrate.sample(_env(tmp_path), group="Other", collections_cfg=spec,
                           limit=None, statuses=["Extracted"], prompt_template="{group} {collections}")
    o_pids = [i["page_id"] for i in json.loads((tmp_path / "calibrate" / "sample.json").read_text())["items"]]
    assert n_o == 1 and o_pids == ["cross"]
