from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.auth import AuthUser, get_current_user
from app.db import get_profile_by_user_id, get_supabase
from app.services.sms import send_sms

router = APIRouter(prefix="/medicines", tags=["medicines"])


class LogDoseBody(BaseModel):
    status: str  # taken | skipped
    scheduled_at: datetime


@router.post("/{medicine_id}/log")
def log_dose(
    medicine_id: str,
    body: LogDoseBody,
    user: AuthUser = Depends(get_current_user),
):
    profile = get_profile_by_user_id(user.user_id)
    sb = get_supabase()
    row = {
        "medicine_id": medicine_id,
        "patient_id": profile["id"],
        "scheduled_at": body.scheduled_at.isoformat(),
        "status": body.status,
    }
    res = sb.table("dose_logs").upsert(row, on_conflict="medicine_id,scheduled_at").execute()
    return {"ok": True, "log": res.data}


@router.get("/adherence")
def adherence(user: AuthUser = Depends(get_current_user)):
    profile = get_profile_by_user_id(user.user_id)
    sb = get_supabase()
    logs = (
        sb.table("dose_logs")
        .select("status,scheduled_at,medicine_id")
        .eq("patient_id", profile["id"])
        .order("scheduled_at", desc=True)
        .limit(200)
        .execute()
    ).data or []
    total = len(logs)
    taken = sum(1 for l in logs if l["status"] == "taken")
    skipped = sum(1 for l in logs if l["status"] == "skipped")
    missed = sum(1 for l in logs if l["status"] == "missed")
    rate = (taken / total) if total else None
    return {
        "total": total,
        "taken": taken,
        "skipped": skipped,
        "missed": missed,
        "adherence_rate": rate,
        "logs": logs,
    }


@router.post("/check-missed")
def check_missed_doses(
    grace_minutes: int = 60,
    user: AuthUser = Depends(get_current_user),
):
    """
    Checks active medicines scheduled for today. If current time is past
    scheduled_at + grace_minutes and no dose log exists, records dose as missed
    and sends a missed-dose SMS alert to caregivers / emergency contacts.
    """
    profile = get_profile_by_user_id(user.user_id)
    sb = get_supabase()
    pid = profile["id"]
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)

    meds = (
        sb.table("medicines")
        .select("id,name,dose,schedule_times")
        .eq("patient_id", pid)
        .eq("active", True)
        .execute()
        .data
        or []
    )

    existing_logs = (
        sb.table("dose_logs")
        .select("medicine_id,scheduled_at,status")
        .eq("patient_id", pid)
        .gte("scheduled_at", today_start.isoformat())
        .execute()
        .data
        or []
    )

    contacts = (
        sb.table("emergency_contacts")
        .select("phone,full_name")
        .eq("patient_id", pid)
        .order("sort_order")
        .limit(2)
        .execute()
        .data
        or []
    )

    missed_alerts = []
    for m in meds:
        times = m.get("schedule_times") or []
        for t_str in times:
            parts = str(t_str).split(":")
            if len(parts) < 2:
                continue
            h, minute = int(parts[0]), int(parts[1])
            sched_time = today_start.replace(hour=h, minute=minute)
            if now > (sched_time + timedelta(minutes=grace_minutes)):
                # Check if already logged
                logged = False
                for log in existing_logs:
                    if log.get("medicine_id") == m["id"]:
                        log_time_str = log.get("scheduled_at", "")
                        try:
                            log_dt = datetime.fromisoformat(log_time_str.replace("Z", "+00:00"))
                            if log_dt.hour == h and log_dt.minute == minute:
                                logged = True
                                break
                        except Exception:
                            pass

                if not logged:
                    # Record as missed
                    sb.table("dose_logs").upsert(
                        {
                            "medicine_id": m["id"],
                            "patient_id": pid,
                            "scheduled_at": sched_time.isoformat(),
                            "status": "missed",
                        },
                        on_conflict="medicine_id,scheduled_at",
                    ).execute()

                    alert_msg = (
                        f"TransplantCare Alert: {profile.get('full_name')} may have missed "
                        f"their scheduled dose of {m.get('name')} ({m.get('dose')}) "
                        f"at {h:02d}:{minute:02d}. Please check on them."
                    )
                    sms_sent = []
                    for c in contacts:
                        sms_sent.append(send_sms(c["phone"], alert_msg))

                    missed_alerts.append(
                        {
                            "medicine_id": m["id"],
                            "medicine_name": m.get("name"),
                            "dose": m.get("dose"),
                            "scheduled_at": sched_time.isoformat(),
                            "sms_sent": sms_sent,
                        }
                    )

    return {"ok": True, "missed_doses": missed_alerts}
