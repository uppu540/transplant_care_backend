"""Claude tool definitions and executors scoped to a patient."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.repository import MongoRepository

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


async def get_daily_budget(
    patient_id: str,
    repository: MongoRepository,
) -> dict[str, Any]:
    return await repository.get_daily_budget(patient_id)


async def execute_tool(
    patient_id: str,
    name: str,
    args: dict[str, Any],
    repository: MongoRepository,
) -> Any:
    if name == "get_medicines":
        return await repository.find_patient_records(
            "medicines",
            patient_id,
            filters={"active": True},
            projection={
                "name": 1,
                "dose": 1,
                "schedule_times": 1,
                "notes": 1,
                "active": 1,
            },
        )

    if name == "log_dose":
        status = args["status"]
        if status not in ("taken", "skipped"):
            return {"error": "status must be taken or skipped"}
        row = {
            "medicine_id": args["medicine_id"],
            "scheduled_at": args["scheduled_at"],
            "status": status,
        }
        log = await repository.upsert_patient_record(
            "dose_logs",
            patient_id,
            {
                "medicine_id": args["medicine_id"],
                "scheduled_at": args["scheduled_at"],
            },
            row,
        )
        return {"ok": True, "log": [log]}

    if name == "get_labs":
        limit = int(args.get("limit") or 20)
        return await repository.find_patient_records(
            "lab_values",
            patient_id,
            projection={
                "analyte": 1,
                "value": 1,
                "unit": 1,
                "ref_low": 1,
                "ref_high": 1,
                "out_of_range": 1,
                "measured_at": 1,
            },
            sort=[("measured_at", -1)],
            limit=limit,
        )

    if name == "log_food":
        row = {
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
        log = await repository.insert_patient_record("food_logs", patient_id, row)
        return {"ok": True, "log": [log]}

    if name == "get_daily_budget":
        return await get_daily_budget(patient_id, repository)

    if name == "send_doctor_message":
        profile = await repository.get_profile_by_id(patient_id)
        if not profile:
            return {"error": "Patient profile not found"}
        meds = await repository.find_patient_records(
            "medicines",
            patient_id,
            filters={"active": True},
            projection={"name": 1, "dose": 1},
        )
        labs = await repository.find_patient_records(
            "lab_values",
            patient_id,
            projection={"analyte": 1, "value": 1, "unit": 1, "measured_at": 1},
            sort=[("measured_at", -1)],
            limit=8,
        )
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
