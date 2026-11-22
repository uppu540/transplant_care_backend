from app.ai.red_flags import detect_red_flag, red_flag_message


def test_fever_english():
    assert detect_red_flag("I have a fever since morning")


def test_chest_pain():
    assert detect_red_flag("sudden chest pain and breathlessness")


def test_safe_message():
    assert not detect_red_flag("What is left in my potassium budget today?")


def test_hindi_fever():
    assert detect_red_flag("मुझे बुखार है")


def test_reply_locale():
    assert "doctor" in red_flag_message("en").lower()
