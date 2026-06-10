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
