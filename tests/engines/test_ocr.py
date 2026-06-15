from insta_save.engines import ocr


def test_ocr_score_averages_box_confidences():
    rapid_result = [[None, "hello", 0.9], [None, "world", 0.7]]
    text, conf = ocr.ocr_score(rapid_result)
    assert text == "hello\nworld"
    assert abs(conf - 0.8) < 1e-9


def test_ocr_score_empty():
    assert ocr.ocr_score(None) == ("", None)
    assert ocr.ocr_score([]) == ("", None)


def test_slide_record_shape():
    rec = ocr.slide_record(2, "txt", 0.5, image="slides/ab/slide2.jpg")
    assert rec == {"slide": 2, "text": "txt", "ocr_confidence": 0.5,
                   "image": "slides/ab/slide2.jpg"}


def test_slide_record_empty_text_is_none():
    assert ocr.slide_record(1, "", None)["text"] is None


def test_ocr_score_text_without_scores():
    # boxes have text but no confidence score -> text returned, confidence None
    assert ocr.ocr_score([[None, "hello", None]]) == ("hello", None)
