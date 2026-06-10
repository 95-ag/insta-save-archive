from insta_save.engines import transcript as tr


def test_gate_rejects_empty():
    assert tr._gate("", 0.99) is False


def test_gate_rejects_too_few_words():
    assert tr._gate("hi there", 0.99) is False  # < 3 words


def test_gate_rejects_low_language_prob():
    assert tr._gate("one two three four", 0.40) is False


def test_gate_accepts_good():
    assert tr._gate("one two three four", 0.80) is True


def test_gate_accepts_exactly_three_words():
    assert tr._gate("one two three", 0.99) is True


def test_gate_accepts_language_prob_at_threshold():
    assert tr._gate("one two three", 0.5) is True
