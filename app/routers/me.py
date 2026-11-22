from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.ai.tools import get_daily_budget
from app.auth import AuthUser, get_current_user
from app.db import get_profile_by_user_id, get_supabase

router = APIRouter(prefix="/me", tags=["me"])


class ConsentBody(BaseModel):
    version: str
    accepted: bool = True


@router.get("/budget/today")
def budget_today(user: AuthUser = Depends(get_current_user)):
    try:
        profile = get_profile_by_user_id(user.user_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return get_daily_budget(profile["id"])


@router.get("/summary")
def clinical_summary(user: AuthUser = Depends(get_current_user)):
    profile = get_profile_by_user_id(user.user_id)
    sb = get_supabase()
    meds = (
        sb.table("medicines")
        .select("name,dose,schedule_times")
        .eq("patient_id", profile["id"])
        .eq("active", True)
        .execute()
    ).data
    labs = (
        sb.table("lab_values")
        .select("analyte,value,unit,measured_at,out_of_range")
        .eq("patient_id", profile["id"])
        .order("measured_at", desc=True)
        .limit(10)
        .execute()
    ).data
    return {"profile": profile, "medicines": meds, "labs": labs}


@router.post("/consent")
def record_consent(body: ConsentBody, user: AuthUser = Depends(get_current_user)):
    sb = get_supabase()
    sb.table("consents").insert(
        {
            "user_id": user.user_id,
            "policy_version": body.version,
            "accepted": body.accepted,
        }
    ).execute()
    return {"ok": True}


@router.get("/export")
def export_data(user: AuthUser = Depends(get_current_user)):
    profile = get_profile_by_user_id(user.user_id)
    pid = profile["id"]
    sb = get_supabase()
    tables = [
        "emergency_contacts",
        "medicines",
        "dose_logs",
        "dietary_limits",
        "food_logs",
        "appointments",
        "lab_reports",
        "lab_values",
        "chat_messages",
        "sos_events",
    ]
    dump = {"profile": profile}
    for t in tables:
        dump[t] = sb.table(t).select("*").eq("patient_id", pid).execute().data
    return dump


class AcceptInviteBody(BaseModel):
    invite_code: str


@router.post("/caregiver/invite")
def create_caregiver_invite(user: AuthUser = Depends(get_current_user)):
    profile = get_profile_by_user_id(user.user_id)
    sb = get_supabase()
    import secrets
    code = secrets.token_hex(4).upper()
    link = sb.table("caregiver_links").insert(
        {
            "patient_id": profile["id"],
            "invite_code": code,
            "status": "pending",
        }
    ).execute()
    return {
        "ok": True,
        "invite_code": code,
        "link": f"transplantcare://invite/{code}",
        "note": "Share this code with your caregiver to give them read-only view of your medications and budget.",
    }


@router.post("/caregiver/accept")
def accept_caregiver_invite(
    body: AcceptInviteBody, user: AuthUser = Depends(get_current_user)
):
    sb = get_supabase()
    found = (
        sb.table("caregiver_links")
        .select("*")
        .eq("invite_code", body.invite_code.strip().upper())
        .eq("status", "pending")
        .execute()
        .data
    )
    if not found:
        raise HTTPException(404, "Invalid or expired invite code")

    link_id = found[0]["id"]
    sb.table("caregiver_links").update(
        {"caregiver_user_id": user.user_id, "status": "active"}
    ).eq("id", link_id).execute()

    # Also update caregiver profile role to caregiver if patient
    try:
        p = get_profile_by_user_id(user.user_id)
        if p.get("role") == "patient":
            sb.table("profiles").update({"role": "caregiver"}).eq("id", p["id"]).execute()
    except Exception:
        pass

    return {"ok": True, "patient_id": found[0]["patient_id"]}


@router.get("/caregiver/patients")
def list_caregiver_patients(user: AuthUser = Depends(get_current_user)):
    sb = get_supabase()
    links = (
        sb.table("caregiver_links")
        .select("patient_id,created_at")
        .eq("caregiver_user_id", user.user_id)
        .eq("status", "active")
        .execute()
        .data
        or []
    )
    patients = []
    for l in links:
        p = sb.table("profiles").select("*").eq("id", l["patient_id"]).execute().data
        if p:
            patients.append(p[0])
    return {"patients": patients}


@router.get("/caregiver/patient/{patient_id}")
def get_caregiver_patient_view(
    patient_id: str, user: AuthUser = Depends(get_current_user)
):
    sb = get_supabase()
    # Verify link
    link = (
        sb.table("caregiver_links")
        .select("id")
        .eq("patient_id", patient_id)
        .eq("caregiver_user_id", user.user_id)
        .eq("status", "active")
        .execute()
        .data
    )
    if not link:
        raise HTTPException(403, "Not authorized to view this patient")

    patient = sb.table("profiles").select("*").eq("id", patient_id).single().execute().data
    meds = (
        sb.table("medicines")
        .select("id,name,dose,schedule_times")
        .eq("patient_id", patient_id)
        .eq("active", True)
        .execute()
        .data
        or []
    )
    from datetime import datetime, timezone
    today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    logs = (
        sb.table("dose_logs")
        .select("medicine_id,scheduled_at,status")
        .eq("patient_id", patient_id)
        .gte("scheduled_at", today_start.isoformat())
        .execute()
        .data
        or []
    )
    budget = get_daily_budget(patient_id)
    labs = (
        sb.table("lab_values")
        .select("analyte,value,unit,ref_low,ref_high,out_of_range,measured_at")
        .eq("patient_id", patient_id)
        .order("measured_at", desc=True)
        .limit(10)
        .execute()
        .data
        or []
    )
    contacts = (
        sb.table("emergency_contacts")
        .select("full_name,phone,relation,sort_order")
        .eq("patient_id", patient_id)
        .order("sort_order")
        .execute()
        .data
        or []
    )

    return {
        "read_only": True,
        "patient": patient,
        "medicines": meds,
        "today_logs": logs,
        "budget": budget,
        "recent_labs": labs,
        "emergency_contacts": contacts,
        "disclaimer": "Read-only caregiver view. Does not replace medical care.",
    }


@router.delete("")
def delete_account(user: AuthUser = Depends(get_current_user)):
    """Deletes profile (cascades patient data). Auth user deletion is manual/admin."""
    profile = get_profile_by_user_id(user.user_id)
    sb = get_supabase()
    sb.table("profiles").delete().eq("id", profile["id"]).execute()
    return {"ok": True, "note": "Profile and related data deleted."}
