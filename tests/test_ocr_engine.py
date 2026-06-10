from insta_save.engines import ocr


def test_ocr_score_averages_box_confidences():
    rapid_result = [[None, "hello", 0.9], [None, "world", 0.7]]
    text, conf = ocr.ocr_score(rapid_result)
    assert text == "hello\nworld"
    assert abs(conf - 0.8) < 1e-9


def test_ocr_score_empty():
    assert ocr.ocr_score(None) == ("", None)
    assert ocr.ocr_score([]) == ("", None)


def test_needs_vision_on_empty_text():
    assert ocr.needs_vision("", 0.99, threshold=0.6) is True


def test_needs_vision_on_missing_confidence():
    assert ocr.needs_vision("text", None, threshold=0.6) is True


def test_needs_vision_below_threshold():
    assert ocr.needs_vision("text", 0.5, threshold=0.6) is True


def test_needs_vision_ok_above_threshold():
    assert ocr.needs_vision("text", 0.7, threshold=0.6) is False


def test_slide_record_shape():
    rec = ocr.slide_record(2, "txt", 0.5, threshold=0.6)
    assert rec == {"slide": 2, "text": "txt", "ocr_confidence": 0.5, "needs_vision": True}


def test_ocr_score_text_without_scores():
    # boxes have text but no confidence score -> text returned, confidence None
    assert ocr.ocr_score([[None, "hello", None]]) == ("hello", None)
