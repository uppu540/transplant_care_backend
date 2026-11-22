from __future__ import annotations

from datetime import datetime, date, timezone
import re
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from app.auth import AuthUser, get_patient_user
from app.repository import MongoRepository, get_repository

router = APIRouter(prefix="/labs", tags=["labs"])

COMMON_RANGES = {
    "creatinine": (0.6, 1.2, "mg/dL"),
    "egfr": (60.0, 120.0, "mL/min/1.73m²"),
    "potassium": (3.5, 5.0, "mEq/L"),
    "tacrolimus": (5.0, 10.0, "ng/mL"),
    "hemoglobin": (12.0, 16.0, "g/dL"),
    "sodium": (135.0, 145.0, "mEq/L"),
    "phosphorus": (2.5, 4.5, "mg/dL"),
    "bun": (7.0, 20.0, "mg/dL"),
    "wbc": (4.0, 11.0, "10^3/uL"),
}


class LabValueInput(BaseModel):
    analyte: str
    value: float
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    measured_at: str | None = None


class ManualReportInput(BaseModel):
    collected_at: str | None = None
    values: list[LabValueInput]


def _utc_datetime(value: str | date | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    else:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.combine(date.fromisoformat(value), datetime.min.time())
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _extract_from_text(raw_text: str) -> list[dict[str, Any]]:
    """Heuristic extractor for standard lab report texts."""
    extracted = []
    lines = raw_text.splitlines()

    patterns = {
        "creatinine": re.compile(r"\b(?:serum\s+)?creatinine\b[^\d\n\r]*([0-9]+\.?[0-9]*)", re.I),
        "egfr": re.compile(r"\b(?:estimated\s+gfr|egfr)\b[^\d\n\r]*([0-9]+\.?[0-9]*)", re.I),
        "potassium": re.compile(r"\b(?:serum\s+)?potassium\b[^\d\n\r]*([0-9]+\.?[0-9]*)", re.I),
        "tacrolimus": re.compile(r"\b(?:tacrolimus|prograf|fk506)\b[^\d\n\r]*([0-9]+\.?[0-9]*)", re.I),
        "hemoglobin": re.compile(r"\b(?:hemoglobin|hb)\b[^\d\n\r]*([0-9]+\.?[0-9]*)", re.I),
        "sodium": re.compile(r"\b(?:serum\s+)?sodium\b[^\d\n\r]*([0-9]+\.?[0-9]*)", re.I),
        "phosphorus": re.compile(r"\b(?:serum\s+)?phosphorus\b[^\d\n\r]*([0-9]+\.?[0-9]*)", re.I),
        "bun": re.compile(r"\b(?:blood\s+urea\s+nitrogen|bun|urea)\b[^\d\n\r]*([0-9]+\.?[0-9]*)", re.I),
        "wbc": re.compile(r"\b(?:wbc|white\s+blood\s+cells?)\b[^\d\n\r]*([0-9]+\.?[0-9]*)", re.I),
    }

    found_keys = set()
    for line in lines:
        clean_line = re.sub(r"\([^)]*\)", "", line)
        for analyte, pat in patterns.items():
            if analyte in found_keys:
                continue
            m = pat.search(clean_line)
            if m:
                val = float(m.group(1))
                low, high, unit = COMMON_RANGES.get(analyte, (None, None, ""))
                extracted.append(
                    {
                        "analyte": analyte,
                        "value": val,
                        "unit": unit,
                        "ref_low": low,
                        "ref_high": high,
                    }
                )
                found_keys.add(analyte)

    return extracted


@router.post("/extract")
async def extract_labs(
    file: UploadFile | None = File(None),
    report_text: str | None = Form(None),
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    """
    Extract lab report values from uploaded report file or text.
    Grounds values with doctor/reference ranges and flags out-of-range values.
    """
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    pid = profile["id"]

    content_str = ""
    filename = "manual_entry.txt"
    if file is not None:
        filename = file.filename or "upload.txt"
        file_bytes = await file.read()
        try:
            content_str = file_bytes.decode("utf-8", errors="ignore")
        except Exception:
            content_str = ""
    elif report_text:
        content_str = report_text

    extracted = _extract_from_text(content_str)

    # Save report
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    report = await repository.insert_patient_record(
        "lab_reports",
        pid,
        {
            "collected_at": today,
            "storage_path": f"reports/{pid}/{filename}",
            "extracted_at": datetime.now(timezone.utc),
            "extraction_raw": {"filename": filename, "values_count": len(extracted)},
        },
    )
    report_id = report["id"]

    saved_values = []
    for item in extracted:
        val = item["value"]
        low = item.get("ref_low")
        high = item.get("ref_high")
        out = (low is not None and val < low) or (high is not None and val > high)
        v_row = {
            "report_id": report_id,
            "analyte": item["analyte"],
            "value": val,
            "unit": item.get("unit"),
            "ref_low": low,
            "ref_high": high,
            "out_of_range": out,
            "measured_at": today,
        }
        saved_values.append(
            await repository.insert_patient_record("lab_values", pid, v_row)
        )

    return {
        "ok": True,
        "report_id": report_id,
        "extracted_values": saved_values,
        "disclaimer": "Automated extraction is informational. Always verify with your clinical report.",
    }


@router.post("/manual")
async def save_manual_labs(
    body: ManualReportInput,
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    pid = profile["id"]
    collected = _utc_datetime(body.collected_at or date.today())
    report = await repository.insert_patient_record(
        "lab_reports",
        pid,
        {
            "collected_at": collected,
            "storage_path": f"reports/{pid}/manual_{collected.date().isoformat()}.json",
            "extracted_at": datetime.now(timezone.utc),
            "extraction_raw": {"source": "manual"},
        },
    )
    report_id = report["id"]

    saved = []
    for val_input in body.values:
        low = val_input.ref_low
        high = val_input.ref_high
        val = val_input.value
        default_low, default_high, default_unit = COMMON_RANGES.get(
            val_input.analyte.lower(), (None, None, "")
        )
        if low is None:
            low = default_low
        if high is None:
            high = default_high
        unit = val_input.unit or default_unit
        out = (low is not None and val < low) or (high is not None and val > high)

        v_row = {
            "report_id": report_id,
            "analyte": val_input.analyte.lower(),
            "value": val,
            "unit": unit,
            "ref_low": low,
            "ref_high": high,
            "out_of_range": out,
            "measured_at": _utc_datetime(val_input.measured_at or collected),
        }
        saved.append(await repository.insert_patient_record("lab_values", pid, v_row))

    return {"ok": True, "report_id": report_id, "values": saved}


@router.get("/trends")
async def get_lab_trends(
    analyte: str | None = None,
    limit: int = 100,
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    """
    Returns time series lab values grouped by analyte with reference ranges
    and out-of-range flags for charting.
    """
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    pid = profile["id"]
    filters = {"analyte": analyte.lower()} if analyte else None
    data = await repository.find_patient_records(
        "lab_values",
        pid,
        filters=filters,
        projection={
            "analyte": 1,
            "value": 1,
            "unit": 1,
            "ref_low": 1,
            "ref_high": 1,
            "out_of_range": 1,
            "measured_at": 1,
        },
        sort=[("measured_at", 1)],
        limit=limit,
    )

    # Group by analyte
    trends: dict[str, list[dict[str, Any]]] = {}
    for row in data:
        a = row.get("analyte", "other")
        val = float(row.get("value", 0))
        low = row.get("ref_low")
        high = row.get("ref_high")
        is_out = (low is not None and val < low) or (high is not None and val > high)
        entry = {
            "measured_at": row.get("measured_at"),
            "value": val,
            "unit": row.get("unit"),
            "ref_low": low,
            "ref_high": high,
            "out_of_range": is_out or row.get("out_of_range", False),
        }
        trends.setdefault(a, []).append(entry)

    return {
        "patient_id": pid,
        "trends": trends,
        "disclaimer": "Lab trends are informational only. Consult your doctor for medical assessment.",
    }


@router.get("/reports")
async def list_reports(
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    reps = await repository.find_patient_records(
        "lab_reports",
        profile["id"],
        sort=[("collected_at", -1)],
        limit=30,
    )
    return {"reports": reps}
