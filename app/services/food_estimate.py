from __future__ import annotations

import json
from typing import Any
import httpx

from app.config import get_settings
from app.db import get_supabase

ESTIMATE_PROMPT = """
You estimate nutrients for a kidney transplant patient's meal.
Ground estimates on the provided Indian food composition rows when possible.
Return ONLY JSON with keys:
description, potassium_mg, sodium_mg, phosphorus_mg, protein_g, fluid_ml, calories_kcal, matched_foods.
Be conservative; prefer slightly over-estimating potassium/sodium/phosphorus for safety.
Do not give medical advice.
""".strip()


def estimate_from_text(text: str) -> dict[str, Any]:
    sb = get_supabase()
    foods = (
        sb.table("food_composition")
        .select("name_en,potassium_mg,sodium_mg,phosphorus_mg,protein_g,fluid_ml,calories_kcal")
        .limit(50)
        .execute()
    ).data or []

    settings = get_settings()
    if not settings.anthropic_api_key:
        return _heuristic(text, foods)

    try:
        headers = {
            "x-api-key": settings.anthropic_api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }
        payload = {
            "model": settings.claude_model,
            "max_tokens": 600,
            "system": ESTIMATE_PROMPT,
            "messages": [
                {
                    "role": "user",
                    "content": (
                        f"Meal description: {text}\n"
                        f"Food DB sample: {json.dumps(foods)[:6000]}"
                    ),
                }
            ],
        }
        with httpx.Client(timeout=25) as http:
            r = http.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
            raw = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
    except Exception:
        return _heuristic(text, foods)

    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _heuristic(text, foods)
    data["estimate_source"] = "llm"
    return data


def _heuristic(text: str, foods: list[dict]) -> dict[str, Any]:
    """Fallback: match composition rows by word overlap and sum nutrients."""
    import re
    text_words = set(re.findall(r"\w+", text.lower()))
    stopwords = {"and", "with", "a", "an", "the", "in", "of", "cooked", "raw", "fresh", "boiled", "medium", "small", "large"}
    matched = []
    for f in foods:
        base_name = f["name_en"].split("(")[0].strip().lower()
        food_words = set(re.findall(r"\w+", base_name)) - stopwords
        if base_name in text.lower() or (food_words and food_words.intersection(text_words)):
            matched.append(f)

    if not matched:
        matched = foods[:1] if foods else []

    def s(key: str) -> float:
        return float(sum((m.get(key) or 0) for m in matched))

    return {
        "description": text,
        "potassium_mg": s("potassium_mg"),
        "sodium_mg": s("sodium_mg"),
        "phosphorus_mg": s("phosphorus_mg"),
        "protein_g": s("protein_g"),
        "fluid_ml": s("fluid_ml"),
        "calories_kcal": s("calories_kcal"),
        "matched_foods": [m["name_en"] for m in matched],
        "estimate_source": "composition",
    }
