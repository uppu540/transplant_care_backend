from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.auth import AuthUser, get_patient_user
from app.models import UTCDateTime
from app.repository import MongoRepository, get_repository
from app.services.sms import send_sms

router = APIRouter(prefix="/medicines", tags=["medicines"])


class LogDoseBody(BaseModel):
    status: Literal["taken", "skipped"]
    scheduled_at: UTCDateTime


@router.post("/{medicine_id}/log")
async def log_dose(
    medicine_id: str,
    body: LogDoseBody,
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    patient_id = profile["id"]
    medicine = await repository.find_one_patient_record(
        "medicines",
        patient_id,
        filters={"_id": medicine_id},
    )
    if not medicine:
        raise HTTPException(404, "Medicine not found")
    log = await repository.upsert_patient_record(
        "dose_logs",
        patient_id,
        {
            "medicine_id": medicine_id,
            "scheduled_at": body.scheduled_at,
        },
        {
            "medicine_id": medicine_id,
            "scheduled_at": body.scheduled_at,
            "status": body.status,
        },
    )
    return {"ok": True, "log": [log]}


@router.get("/adherence")
async def adherence(
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    logs = await repository.find_patient_records(
        "dose_logs",
        profile["id"],
        projection={"status": 1, "scheduled_at": 1, "medicine_id": 1},
        sort=[("scheduled_at", -1)],
        limit=200,
    )
    total = len(logs)
    taken = sum(1 for log in logs if log["status"] == "taken")
    skipped = sum(1 for log in logs if log["status"] == "skipped")
    missed = sum(1 for log in logs if log["status"] == "missed")
    return {
        "total": total,
        "taken": taken,
        "skipped": skipped,
        "missed": missed,
        "adherence_rate": (taken / total) if total else None,
        "logs": logs,
    }


@router.post("/check-missed")
async def check_missed_doses(
    grace_minutes: int = 60,
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    """
    Checks active medicines scheduled for today. If current time is past
    scheduled_at + grace_minutes and no dose log exists, records dose as missed
    and sends a missed-dose SMS alert to caregivers / emergency contacts.
    """
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    patient_id = profile["id"]
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    meds = await repository.find_patient_records(
        "medicines",
        patient_id,
        filters={"active": True},
        projection={"id": 1, "name": 1, "dose": 1, "schedule_times": 1},
    )
    existing_logs = await repository.find_patient_records(
        "dose_logs",
        patient_id,
        filters={"scheduled_at": {"$gte": today_start}},
        projection={"medicine_id": 1, "scheduled_at": 1, "status": 1},
    )
    contacts = await repository.find_patient_records(
        "emergency_contacts",
        patient_id,
        projection={"phone": 1, "full_name": 1},
        sort=[("sort_order", 1)],
        limit=2,
    )
    logged_slots = set()
    for log in existing_logs:
        scheduled = log.get("scheduled_at")
        if isinstance(scheduled, datetime):
            logged_slots.add(
                (log.get("medicine_id"), scheduled.hour, scheduled.minute)
            )

    missed_alerts = []
    for medicine in meds:
        for time_value in medicine.get("schedule_times") or []:
            parts = str(time_value).split(":")
            if len(parts) < 2:
                continue
            try:
                hour, minute = int(parts[0]), int(parts[1])
                scheduled = today_start.replace(hour=hour, minute=minute)
            except ValueError:
                continue
            if now <= scheduled + timedelta(minutes=grace_minutes):
                continue
            slot = (medicine["id"], hour, minute)
            if slot in logged_slots:
                continue

            await repository.upsert_patient_record(
                "dose_logs",
                patient_id,
                {
                    "medicine_id": medicine["id"],
                    "scheduled_at": scheduled,
                },
                {
                    "medicine_id": medicine["id"],
                    "scheduled_at": scheduled,
                    "status": "missed",
                },
            )
            logged_slots.add(slot)
            alert_msg = (
                f"TransplantCare Alert: {profile.get('full_name')} may have missed "
                f"their scheduled dose of {medicine.get('name')} ({medicine.get('dose')}) "
                f"at {hour:02d}:{minute:02d}. Please check on them."
            )
            sms_sent = [
                await run_in_threadpool(send_sms, contact["phone"], alert_msg)
                for contact in contacts
            ]
            missed_alerts.append(
                {
                    "medicine_id": medicine["id"],
                    "medicine_name": medicine.get("name"),
                    "dose": medicine.get("dose"),
                    "scheduled_at": scheduled.isoformat(),
                    "sms_sent": sms_sent,
                }
            )

    return {"ok": True, "missed_doses": missed_alerts}
