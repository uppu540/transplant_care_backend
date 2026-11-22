from __future__ import annotations

import json
from typing import Any
import httpx

from app.ai.red_flags import detect_red_flag, red_flag_message
from app.ai.system_prompt import SYSTEM_PROMPT
from app.ai.tools import TOOL_DEFINITIONS, execute_tool
from app.config import get_settings
from app.repository import MongoRepository


def _call_claude(
    api_key: str,
    model: str,
    system: str,
    tools: list[dict],
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 1024,
        "system": system,
        "tools": tools,
        "messages": messages,
    }
    with httpx.Client(timeout=30) as client:
        r = client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
        r.raise_for_status()
        data = r.json()
        return data.get("content", [])


async def run_chat(
    patient_id: str,
    message: str,
    repository: MongoRepository,
    locale: str = "en",
) -> dict[str, Any]:
    if detect_red_flag(message):
        reply = red_flag_message(locale)
        await _persist(
            patient_id,
            message,
            reply,
            red_flag=True,
            tools_used=[],
            repository=repository,
        )
        return {"reply": reply, "red_flag": True, "tools_used": []}

    settings = get_settings()
    if not settings.anthropic_api_key:
        reply = (
            "AI assistant is not configured yet. "
            "Please use Medicines, Food, Contact Doctor, or SOS. "
            "This app does not replace medical care."
        )
        return {"reply": reply, "red_flag": False, "tools_used": []}

    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": f"[locale={locale}]\n{message}",
        }
    ]

    tools_used: list[str] = []
    # Tool loop (max 5 rounds)
    for _ in range(5):
        try:
            content_blocks = _call_claude(
                api_key=settings.anthropic_api_key,
                model=settings.claude_model,
                system=SYSTEM_PROMPT,
                tools=TOOL_DEFINITIONS,
                messages=messages,
            )
        except Exception as exc:
            fallback = "I could not reach the AI service right now. Please try again or contact your doctor."
            await _persist(
                patient_id,
                message,
                fallback,
                red_flag=False,
                tools_used=tools_used,
                repository=repository,
            )
            return {"reply": fallback, "red_flag": False, "tools_used": tools_used}

        tool_uses = [b for b in content_blocks if b.get("type") == "tool_use"]
        if not tool_uses:
            text = "".join(b.get("text", "") for b in content_blocks if b.get("type") == "text")
            red = detect_red_flag(message) or detect_red_flag(text)
            if red:
                text = red_flag_message(locale)
            await _persist(
                patient_id,
                message,
                text,
                red_flag=red,
                tools_used=tools_used,
                repository=repository,
            )
            return {"reply": text, "red_flag": red, "tools_used": tools_used}

        # Append assistant turn then tool results
        messages.append({"role": "assistant", "content": content_blocks})
        tool_results = []
        for block in tool_uses:
            name = block.get("name")
            tool_id = block.get("id")
            tool_input = block.get("input") or {}
            tools_used.append(name)
            result = await execute_tool(patient_id, name, tool_input, repository)
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": tool_id,
                    "content": json.dumps(result, default=str),
                }
            )
        messages.append({"role": "user", "content": tool_results})

    fallback = "I could not finish that request. Please try again or contact your doctor."
    await _persist(
        patient_id,
        message,
        fallback,
        red_flag=False,
        tools_used=tools_used,
        repository=repository,
    )
    return {"reply": fallback, "red_flag": False, "tools_used": tools_used}


async def _persist(
    patient_id: str,
    user_msg: str,
    assistant_msg: str,
    red_flag: bool,
    tools_used: list[str],
    repository: MongoRepository,
) -> None:
    await repository.insert_patient_record(
        "chat_messages",
        patient_id,
        {"role": "user", "content": user_msg},
    )
    await repository.insert_patient_record(
        "chat_messages",
        patient_id,
        {
            "role": "assistant",
            "content": assistant_msg,
            "red_flag": red_flag,
            "tools_used": tools_used,
        },
    )
