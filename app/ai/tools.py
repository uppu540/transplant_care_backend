"""Claude tool definitions and executors scoped to a patient."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from app.db import get_supabase

TOOL_DEFINITIONS = [
    {
        "name": "get_medicines",
        "description": "List the patient's active medicines and schedule times.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "log_dose",
        "description": (
            "Record that the patient already took or skipped a dose. "
            "Only use when the patient clearly states what they did. "
            "Never suggest skipping."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "medicine_id": {"type": "string"},
                "scheduled_at": {"type": "string", "description": "ISO datetime"},
                "status": {"type": "string", "enum": ["taken", "skipped"]},
            },
            "required": ["medicine_id", "scheduled_at", "status"],
        },
    },
    {
        "name": "get_labs",
        "description": "Recent lab values for the patient.",
        "input_schema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "default": 20}},
        },
    },
    {
        "name": "log_food",
        "description": "Log a meal with estimated nutrients (mg/g/ml/kcal).",
        "input_schema": {
            "type": "object",
            "properties": {
                "description": {"type": "string"},
                "meal_type": {
                    "type": "string",
                    "enum": ["breakfast", "lunch", "dinner", "snack", "other"],
                },
                "potassium_mg": {"type": "number"},
                "sodium_mg": {"type": "number"},
                "phosphorus_mg": {"type": "number"},
                "protein_g": {"type": "number"},
                "fluid_ml": {"type": "number"},
                "calories_kcal": {"type": "number"},
            },
            "required": ["description"],
        },
    },
    {
        "name": "get_daily_budget",
        "description": (
            "Doctor-set dietary limits vs today's consumption. "
            "If limits are null, tell the user to set doctor limits — do not invent."
        ),
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "send_doctor_message",
        "description": "Build a WhatsApp wa.me URL with a clinical summary for the doctor.",
        "input_schema": {
            "type": "object",
            "properties": {"symptoms": {"type": "string"}},
        },
    },
]


def _today_bounds_iso() -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = now.replace(hour=23, minute=59, second=59, microsecond=999999)
    return start.isoformat(), end.isoformat()


def get_daily_budget(patient_id: str) -> dict[str, Any]:
    sb = get_supabase()
    limits_res = (
        sb.table("dietary_limits").select("*").eq("patient_id", patient_id).limit(1).execute()
    )
    limits = limits_res.data[0] if limits_res.data else None
    start, end = _today_bounds_iso()
    foods = (
        sb.table("food_logs")
        .select(
            "potassium_mg,sodium_mg,phosphorus_mg,protein_g,fluid_ml,calories_kcal"
        )
        .eq("patient_id", patient_id)
        .gte("logged_at", start)
        .lte("logged_at", end)
        .execute()
    )

    def sum_field(key: str) -> float:
        return float(sum((row.get(key) or 0) for row in (foods.data or [])))

    consumed = {
        "potassium_mg": sum_field("potassium_mg"),
        "sodium_mg": sum_field("sodium_mg"),
        "phosphorus_mg": sum_field("phosphorus_mg"),
        "protein_g": sum_field("protein_g"),
        "fluid_ml": sum_field("fluid_ml"),
        "calories_kcal": sum_field("calories_kcal"),
    }

    remaining = None
    if limits:
        remaining = {}
        for key in consumed:
            limit_val = limits.get(key)
            remaining[key] = None if limit_val is None else float(limit_val) - consumed[key]

    return {
        "limits_set": limits is not None,
        "limits": limits,
        "consumed": consumed,
        "remaining": remaining,
    }


def execute_tool(patient_id: str, name: str, args: dict[str, Any]) -> Any:
    sb = get_supabase()

    if name == "get_medicines":
        res = (
            sb.table("medicines")
            .select("id,name,dose,schedule_times,notes,active")
            .eq("patient_id", patient_id)
            .eq("active", True)
            .execute()
        )
        return res.data

    if name == "log_dose":
        status = args["status"]
        if status not in ("taken", "skipped"):
            return {"error": "status must be taken or skipped"}
        row = {
            "medicine_id": args["medicine_id"],
            "patient_id": patient_id,
            "scheduled_at": args["scheduled_at"],
            "status": status,
        }
        res = sb.table("dose_logs").upsert(row, on_conflict="medicine_id,scheduled_at").execute()
        return {"ok": True, "log": res.data}

    if name == "get_labs":
        limit = int(args.get("limit") or 20)
        res = (
            sb.table("lab_values")
            .select("analyte,value,unit,ref_low,ref_high,out_of_range,measured_at")
            .eq("patient_id", patient_id)
            .order("measured_at", desc=True)
            .limit(limit)
            .execute()
        )
        return res.data

    if name == "log_food":
        row = {
            "patient_id": patient_id,
            "description": args["description"],
            "meal_type": args.get("meal_type") or "other",
            "potassium_mg": args.get("potassium_mg"),
            "sodium_mg": args.get("sodium_mg"),
            "phosphorus_mg": args.get("phosphorus_mg"),
            "protein_g": args.get("protein_g"),
            "fluid_ml": args.get("fluid_ml"),
            "calories_kcal": args.get("calories_kcal"),
            "estimate_source": "llm",
        }
        res = sb.table("food_logs").insert(row).execute()
        return {"ok": True, "log": res.data}

    if name == "get_daily_budget":
        return get_daily_budget(patient_id)

    if name == "send_doctor_message":
        profile = (
            sb.table("profiles")
            .select("full_name,doctor_name,doctor_whatsapp,transplant_date")
            .eq("id", patient_id)
            .single()
            .execute()
        ).data
        meds = (
            sb.table("medicines")
            .select("name,dose")
            .eq("patient_id", patient_id)
            .eq("active", True)
            .execute()
        ).data
        labs = (
            sb.table("lab_values")
            .select("analyte,value,unit,measured_at")
            .eq("patient_id", patient_id)
            .order("measured_at", desc=True)
            .limit(8)
            .execute()
        ).data
        phone = (profile.get("doctor_whatsapp") or "").lstrip("+").replace(" ", "")
        if not phone:
            return {"error": "Doctor WhatsApp number not set"}
        meds_line = ", ".join(f"{m['name']} {m['dose']}" for m in (meds or [])) or "n/a"
        labs_line = ", ".join(
            f"{l['analyte']}={l['value']}{l.get('unit') or ''}" for l in (labs or [])
        ) or "n/a"
        symptoms = args.get("symptoms") or "Not specified"
        text = (
            f"Hello Dr. {profile.get('doctor_name') or ''},\n"
            f"Patient: {profile.get('full_name')} "
            f"(transplant {profile.get('transplant_date') or 'n/a'}).\n"
            f"Symptoms: {symptoms}\n"
            f"Recent labs: {labs_line}\n"
            f"Medicines: {meds_line}\n"
            f"— via TransplantCare (does not replace medical care)"
        )
        wa_url = f"https://wa.me/{phone}?text={quote(text)}"
        return {"wa_url": wa_url, "text": text}

    return {"error": f"Unknown tool: {name}"}
