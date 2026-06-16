from insta_save.engines.ocr_clean import clean_ocr_text


def test_exact_repeats_collapse_to_first():
    text = "Block dangerous prod\nBlock dangerous prod\nunique tail line"
    assert clean_ocr_text(text) == "Block dangerous prod\nunique tail line"


def test_near_duplicate_frame_lines_collapse():
    text = ("the quick brown fox jumps over the lazy dog\n"
            "the quick brown fox jumps ouer the lazy dog\n"   # OCR jitter near-dup
            "a totally separate caption")
    assert clean_ocr_text(text).splitlines() == [
        "the quick brown fox jumps over the lazy dog",
        "a totally separate caption",
    ]


def test_distinct_lines_all_preserved():
    text = "alpha heading\nbeta body text\ngamma footer note"
    assert clean_ocr_text(text).splitlines() == [
        "alpha heading", "beta body text", "gamma footer note"]


def test_garbage_lines_dropped_but_short_tokens_kept():
    text = "目\n★\nM\n·\n::\n01\nAI\nReal heading here"
    assert clean_ocr_text(text).splitlines() == ["01", "AI", "Real heading here"]


def test_slide_markers_always_preserved():
    # normalized 'slide1'/'slide2' are >0.8 similar — must NOT collapse (carousel structure).
    text = "[Slide 1]\n[Slide 2]\n[Slide 3]"
    assert clean_ocr_text(text).splitlines() == ["[Slide 1]", "[Slide 2]", "[Slide 3]"]


def test_short_numeric_tokens_not_fuzzy_collapsed():
    assert clean_ocr_text("01\n02\n03").splitlines() == ["01", "02", "03"]


def test_empty_and_whitespace_only():
    assert clean_ocr_text("") == ""
    assert clean_ocr_text("   \n  \n\t") == ""


# --- max_chars cap tests -------------------------------------------------------

def test_cap_trims_to_whole_lines_within_limit():
    # Build a cleaned text that will be >100 chars when joined, then verify cap.
    line_a = "a" * 40   # 40 chars
    line_b = "b" * 40   # 40 chars (total joined = 81)
    line_c = "c" * 40   # would push to 122
    text = f"{line_a}\n{line_b}\n{line_c}"
    result = clean_ocr_text(text, max_chars=100)
    assert len(result) <= 100
    # Must end on a whole line (no mid-line cut)
    full_cleaned = clean_ocr_text(text)
    assert full_cleaned.startswith(result)
    # result itself must be a prefix of the full cleaned output with no partial tail line
    if result:
        assert result == "\n".join(full_cleaned.splitlines()[:len(result.splitlines())])


def test_cap_preserves_slide_markers_within_prefix():
    marker = "[Slide 1]"
    line_a = "a" * 40
    line_b = "b" * 40
    line_c = "c" * 40   # pushed out by cap
    text = f"{marker}\n{line_a}\n{line_b}\n{line_c}"
    # cap at 100 should keep the marker + line_a + line_b (marker=9, line_a=40, line_b=40 → 91 chars)
    result = clean_ocr_text(text, max_chars=100)
    assert "[Slide 1]" in result
    assert len(result) <= 100


def test_cap_default_none_behavior_unchanged():
    # Passing no max_chars must produce the same result as the existing default.
    text = "the quick brown fox jumps over the lazy dog\nsome other distinct line here"
    assert clean_ocr_text(text) == clean_ocr_text(text, max_chars=None)


def test_cap_input_already_under_limit_unchanged():
    text = "short line\nanother line"
    result = clean_ocr_text(text, max_chars=10000)
    assert result == clean_ocr_text(text)
