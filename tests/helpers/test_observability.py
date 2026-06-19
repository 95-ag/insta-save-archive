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
