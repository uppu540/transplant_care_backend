from __future__ import annotations

from datetime import datetime, date, timezone
import json
import re
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel

from app.auth import AuthUser, get_current_user
from app.config import get_settings
from app.db import get_profile_by_user_id, get_supabase

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
    user: AuthUser = Depends(get_current_user),
):
    """
    Extract lab report values from uploaded report file or text.
    Grounds values with doctor/reference ranges and flags out-of-range values.
    """
    profile = get_profile_by_user_id(user.user_id)
    sb = get_supabase()
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
    today_str = date.today().isoformat()
    rep_res = sb.table("lab_reports").insert(
        {
            "patient_id": pid,
            "collected_at": today_str,
            "storage_path": f"reports/{pid}/{filename}",
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "extraction_raw": {"filename": filename, "values_count": len(extracted)},
        }
    ).execute()
    report_id = rep_res.data[0]["id"] if rep_res.data else "mock-rep-id"

    saved_values = []
    for item in extracted:
        val = item["value"]
        low = item.get("ref_low")
        high = item.get("ref_high")
        out = (low is not None and val < low) or (high is not None and val > high)
        v_row = {
            "report_id": report_id,
            "patient_id": pid,
            "analyte": item["analyte"],
            "value": val,
            "unit": item.get("unit"),
            "ref_low": low,
            "ref_high": high,
            "out_of_range": out,
            "measured_at": today_str,
        }
        res = sb.table("lab_values").insert(v_row).execute()
        saved_values.append(res.data[0] if res.data else v_row)

    return {
        "ok": True,
        "report_id": report_id,
        "extracted_values": saved_values,
        "disclaimer": "Automated extraction is informational. Always verify with your clinical report.",
    }


@router.post("/manual")
def save_manual_labs(
    body: ManualReportInput,
    user: AuthUser = Depends(get_current_user),
):
    profile = get_profile_by_user_id(user.user_id)
    sb = get_supabase()
    pid = profile["id"]
    collected = body.collected_at or date.today().isoformat()

    rep_res = sb.table("lab_reports").insert(
        {
            "patient_id": pid,
            "collected_at": collected,
            "storage_path": f"reports/{pid}/manual_{collected}.json",
            "extracted_at": datetime.now(timezone.utc).isoformat(),
            "extraction_raw": {"source": "manual"},
        }
    ).execute()
    report_id = rep_res.data[0]["id"] if rep_res.data else "mock-rep-id"

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
            "patient_id": pid,
            "analyte": val_input.analyte.lower(),
            "value": val,
            "unit": unit,
            "ref_low": low,
            "ref_high": high,
            "out_of_range": out,
            "measured_at": val_input.measured_at or collected,
        }
        res = sb.table("lab_values").insert(v_row).execute()
        saved.append(res.data[0] if res.data else v_row)

    return {"ok": True, "report_id": report_id, "values": saved}


@router.get("/trends")
def get_lab_trends(
    analyte: str | None = None,
    limit: int = 100,
    user: AuthUser = Depends(get_current_user),
):
    """
    Returns time series lab values grouped by analyte with reference ranges
    and out-of-range flags for charting.
    """
    profile = get_profile_by_user_id(user.user_id)
    sb = get_supabase()
    pid = profile["id"]

    query = (
        sb.table("lab_values")
        .select("analyte,value,unit,ref_low,ref_high,out_of_range,measured_at")
        .eq("patient_id", pid)
        .order("measured_at", desc=False)
        .limit(limit)
    )
    if analyte:
        query = query.eq("analyte", analyte.lower())

    data = query.execute().data or []

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
def list_reports(user: AuthUser = Depends(get_current_user)):
    profile = get_profile_by_user_id(user.user_id)
    sb = get_supabase()
    reps = (
        sb.table("lab_reports")
        .select("*")
        .eq("patient_id", profile["id"])
        .order("collected_at", desc=True)
        .limit(30)
        .execute()
        .data
        or []
    )
    return {"reports": reps}
