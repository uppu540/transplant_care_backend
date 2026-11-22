import asyncio

from app.ai.red_flags import detect_red_flag, red_flag_message
from app.ai.system_prompt import SYSTEM_PROMPT
from app.ai.tools import get_daily_budget


def test_red_flag_symptoms_all_escalate():
    symptoms = [
        "I have a fever of 101 since afternoon",
        "Noticeably decreased urine today",
        "Severe graft-site swelling and incision pain",
        "Persistent vomiting after morning medicine",
        "Sudden weight gain of 3kg in two days",
        "Sharp chest pain when breathing",
        "Shortness of breath and breathlessness",
        # Hindi & Telugu
        "मुझे तेज बुखार और ठंड लग रही है",
        "తీవ్రమైన జ్వరం మరియు ఛాతీ నొప్పి వస్తుంది",
    ]
    for s in symptoms:
        assert detect_red_flag(s) is True
        msg = red_flag_message("en")
        assert "contact your doctor now" in msg.lower()
        assert "does not replace" in msg.lower()


def test_safe_symptoms_do_not_trigger_red_flag():
    safe_phrases = [
        "Can I eat watermelon?",
        "What time is my tacrolimus scheduled?",
        "How much protein have I taken today?",
        "Log 2 idlis for breakfast",
    ]
    for p in safe_phrases:
        assert detect_red_flag(p) is False


def test_hard_safety_rule_system_prompt_contains_constraints():
    # Verify non-negotiable constraints exist in system prompt
    assert "NEVER recommend starting, stopping, skipping, or changing any medicine dose" in SYSTEM_PROMPT
    assert "Use ONLY doctor-entered dietary limits" in SYSTEM_PROMPT
    assert "Do not invent numbers" in SYSTEM_PROMPT
    assert "IMMEDIATELY tell them to contact their doctor now" in SYSTEM_PROMPT


def test_budget_null_limits_returns_limits_set_false(mongo_repository):
    # If no doctor limits exist, limits_set must be False so AI cannot guess
    pid = "patient-unconfigured"
    budget = asyncio.run(get_daily_budget(pid, mongo_repository))
    assert budget["limits_set"] is False
    assert budget["limits"] is None
    assert budget["remaining"] is None
