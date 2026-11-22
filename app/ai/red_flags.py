"""Heuristic red-flag detection (EN / HI / TE keywords)."""

from __future__ import annotations

import re

RED_FLAG_PATTERNS: list[re.Pattern[str]] = [
    # English
    re.compile(r"\bfevers?\b", re.I),
    re.compile(r"\b(high\s+temperature|temperature\s+is\s+high)\b", re.I),
    re.compile(r"\b(reduced|less|low|decreased)\s+urine\b", re.I),
    re.compile(r"\b(no|not\s+passing)\s+urine\b", re.I),
    re.compile(r"\b(graft|transplant|incision)\s*(site)?\s*(pain|swelling|swollen)\b", re.I),
    re.compile(r"\b(persistent|continuous|keep)\s+vomit", re.I),
    re.compile(r"\bsudden\s+weight\s+gain\b", re.I),
    re.compile(r"\bchest\s+pain\b", re.I),
    re.compile(r"\b(breathlessness|short(ness)?\s+of\s+breath|can'?t\s+breathe)\b", re.I),
    # Hindi (transliteration + Devanagari snippets)
    re.compile(r"बुखार|पेशाब\s*कम|छाती\s*में\s*दर्द|सांस\s*फूल"),
    re.compile(r"\bbukhar\b", re.I),
    re.compile(r"\bsaans\s*(phool|phoolna)\b", re.I),
    # Telugu
    re.compile(r"జ్వరం|మూత్రం\s*తగ్గ|ఛాతీ\s*నొప్పి|ఊపిరి"),
    re.compile(r"\bjvaram\b", re.I),
    re.compile(r"\buri\s*tippudu\b", re.I),
]

RED_FLAG_REPLY = {
    "en": (
        "These symptoms can be serious after a transplant. "
        "Contact your doctor now. Use WhatsApp or SOS below. "
        "TransplantCare does not replace emergency medical care."
    ),
    "hi": (
        "ये लक्षण ट्रांसप्लांट के बाद गंभीर हो सकते हैं। "
        "अभी अपने डॉक्टर से संपर्क करें। नीचे WhatsApp या SOS उपयोग करें। "
        "TransplantCare आपातकालीन चिकित्सा की जगह नहीं लेता।"
    ),
    "te": (
        "ఈ లక్షణాలు ట్రాన్స్‌ప్లాంట్ తర్వాత తీవ్రంగా ఉండవచ్చు. "
        "ఇప్పుడే మీ డాక్టర్‌ను సంప్రదించండి. కింద WhatsApp లేదా SOS ఉపయోగించండి. "
        "TransplantCare అత్యవసర వైద్య సంరక్షణకు ప్రత్యామ్నాయం కాదు."
    ),
}


def detect_red_flag(text: str) -> bool:
    return any(p.search(text or "") for p in RED_FLAG_PATTERNS)


def red_flag_message(locale: str = "en") -> str:
    return RED_FLAG_REPLY.get(locale, RED_FLAG_REPLY["en"])
