from __future__ import annotations

from datetime import datetime, date, timezone
import re
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel
from pymongo.errors import PyMongoError
from starlette.responses import Response

from app.auth import AuthUser, get_patient_user
from app.repository import MongoRepository, get_repository
from app.storage import (
    MAX_LAB_REPORT_BYTES,
    LabReportStorage,
    get_lab_report_storage,
)

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
    report_id: str | None = None
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
    storage: LabReportStorage = Depends(get_lab_report_storage),
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
    storage_file_id = None
    content_type = None
    if file is not None:
        filename = (file.filename or "upload.txt").replace("\\", "/").rsplit("/", 1)[-1]
        content_type = file.content_type or "application/octet-stream"
        allowed_types = {
            "application/octet-stream",
            "application/pdf",
            "image/jpeg",
            "image/png",
            "image/webp",
            "text/plain",
        }
        if content_type not in allowed_types:
            raise HTTPException(status_code=415, detail="Unsupported lab report file type")
        file_bytes = await file.read(MAX_LAB_REPORT_BYTES + 1)
        if len(file_bytes) > MAX_LAB_REPORT_BYTES:
            raise HTTPException(status_code=413, detail="Lab report file is too large")
        storage_file_id = await storage.upload(
            patient_id=pid,
            filename=filename,
            content_type=content_type,
            content=file_bytes,
        )
        content_str = file_bytes.decode("utf-8", errors="ignore")
    elif report_text:
        content_str = report_text

    extracted = _extract_from_text(content_str)

    # Save report
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        report = await repository.insert_patient_record(
            "lab_reports",
            pid,
            {
                "collected_at": today,
                "storage_path": f"reports/{pid}/{filename}",
                "extracted_at": datetime.now(timezone.utc),
                "extraction_raw": {"filename": filename, "values_count": len(extracted)},
                "storage_file_id": storage_file_id,
                "content_type": content_type,
                "original_filename": filename,
            },
        )
    except PyMongoError:
        if storage_file_id:
            await storage.delete(storage_file_id)
        raise
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
    if body.report_id:
        report = await repository.find_one_patient_record(
            "lab_reports",
            pid,
            filters={"_id": body.report_id},
        )
        if not report:
            raise HTTPException(404, "Lab report not found")
        report_id = body.report_id
        await repository.update_patient_record(
            "lab_reports",
            pid,
            report_id,
            {
                "collected_at": collected,
                "extraction_raw": {"source": "confirmed"},
            },
        )
        await repository.delete_patient_records(
            "lab_values",
            pid,
            {"report_id": report_id},
        )
    else:
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
    for report in reps:
        report.pop("storage_file_id", None)
        report.pop("content_type", None)
        report.pop("original_filename", None)
    return {"reports": reps}


@router.get("/reports/{report_id}/file")
async def download_lab_report(
    report_id: str,
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
    storage: LabReportStorage = Depends(get_lab_report_storage),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    report = await repository.find_one_patient_record(
        "lab_reports",
        profile["id"],
        filters={"_id": report_id},
    )
    if not report or not report.get("storage_file_id"):
        raise HTTPException(404, "Lab report file not found")
    content, metadata = await storage.download(
        report["storage_file_id"],
        profile["id"],
    )
    filename = report.get("original_filename") or "lab-report"
    return Response(
        content=content,
        media_type=metadata.get("content_type") or "application/octet-stream",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
        },
    )
