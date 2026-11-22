from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.auth import AuthUser, get_current_user
from app.db import get_profile_by_user_id, get_supabase
from app.services.sms import send_sms

router = APIRouter(tags=["emergency"])


class DoctorPayload(BaseModel):
    symptoms: str | None = None


class SosBody(BaseModel):
    lat: float | None = None
    lng: float | None = None
    symptoms: str | None = None


def _build_doctor_text(profile: dict, symptoms: str | None) -> tuple[str, str]:
    sb = get_supabase()
    pid = profile["id"]
    meds = (
        sb.table("medicines")
        .select("name,dose")
        .eq("patient_id", pid)
        .eq("active", True)
        .execute()
    ).data
    labs = (
        sb.table("lab_values")
        .select("analyte,value,unit")
        .eq("patient_id", pid)
        .order("measured_at", desc=True)
        .limit(8)
        .execute()
    ).data
    meds_line = ", ".join(f"{m['name']} {m['dose']}" for m in (meds or [])) or "n/a"
    labs_line = ", ".join(
        f"{l['analyte']}={l['value']}{l.get('unit') or ''}" for l in (labs or [])
    ) or "n/a"
    text = (
        f"Hello Dr. {profile.get('doctor_name') or ''},\n"
        f"Patient: {profile.get('full_name')} "
        f"(transplant {profile.get('transplant_date') or 'n/a'}).\n"
        f"Symptoms: {symptoms or 'Not specified'}\n"
        f"Recent labs: {labs_line}\n"
        f"Medicines: {meds_line}\n"
        f"— via TransplantCare (does not replace medical care)"
    )
    phone = (profile.get("doctor_whatsapp") or "").lstrip("+").replace(" ", "")
    if not phone:
        raise HTTPException(400, "Doctor WhatsApp number not set in profile")
    wa_url = f"https://wa.me/{phone}?text={quote(text)}"
    return wa_url, text


@router.post("/doctor/whatsapp-payload")
def doctor_whatsapp(body: DoctorPayload, user: AuthUser = Depends(get_current_user)):
    profile = get_profile_by_user_id(user.user_id)
    wa_url, text = _build_doctor_text(profile, body.symptoms)
    return {"wa_url": wa_url, "text": text}


@router.post("/sos/trigger")
def sos_trigger(body: SosBody, user: AuthUser = Depends(get_current_user)):
    profile = get_profile_by_user_id(user.user_id)
    sb = get_supabase()
    contacts = (
        sb.table("emergency_contacts")
        .select("*")
        .eq("patient_id", profile["id"])
        .order("sort_order")
        .execute()
    ).data or []
    if not contacts:
        raise HTTPException(400, "Add at least one emergency contact first")

    maps = ""
    if body.lat is not None and body.lng is not None:
        maps = f"https://maps.google.com/?q={body.lat},{body.lng}"

    message = (
        f"SOS from {profile.get('full_name')} (TransplantCare). "
        f"Possible emergency. Location: {maps or 'unavailable'}. "
        f"Symptoms: {body.symptoms or 'n/a'}. Please call them now."
    )

    event = (
        sb.table("sos_events")
        .insert(
            {
                "patient_id": profile["id"],
                "lat": body.lat,
                "lng": body.lng,
                "symptoms": body.symptoms,
                "status": "sent",
            }
        )
        .execute()
    ).data

    sms_results = []
    wa_urls = []
    for c in contacts:
        sms_results.append(send_sms(c["phone"], message))
        phone = c["phone"].lstrip("+").replace(" ", "")
        wa_urls.append(
            {
                "name": c["full_name"],
                "wa_url": f"https://wa.me/{phone}?text={quote(message)}",
            }
        )

    first = contacts[0]
    dial_uri = f"tel:{first['phone']}"

    return {
        "event": event,
        "sms_results": sms_results,
        "whatsapp_urls": wa_urls,
        "dial_uri": dial_uri,
        "first_contact": first,
        "disclaimer": "If this is life-threatening, call local emergency services immediately.",
    }


@router.post("/sos/cancel")
def sos_cancel(user: AuthUser = Depends(get_current_user)):
    profile = get_profile_by_user_id(user.user_id)
    sb = get_supabase()
    # Mark latest pending/triggered as cancelled (client countdown cancel)
    latest = (
        sb.table("sos_events")
        .select("id")
        .eq("patient_id", profile["id"])
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    ).data
    if latest:
        sb.table("sos_events").update({"status": "cancelled"}).eq(
            "id", latest[0]["id"]
        ).execute()
    return {"ok": True}
