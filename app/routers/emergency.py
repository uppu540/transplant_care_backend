import asyncio
import logging
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from app.auth import AuthUser, get_patient_user
from app.repository import MongoRepository, get_repository
from app.services.sms import send_sms

router = APIRouter(tags=["emergency"])
logger = logging.getLogger(__name__)


class DoctorPayload(BaseModel):
    symptoms: str | None = None


class SosBody(BaseModel):
    lat: float | None = None
    lng: float | None = None
    symptoms: str | None = None


async def _send_sms_safely(phone: str, message: str) -> dict:
    try:
        return await run_in_threadpool(send_sms, phone, message)
    except httpx.HTTPError as error:
        logger.warning("SOS SMS provider request failed: %s", type(error).__name__)
        return {"ok": False, "error": "SMS provider unavailable"}


async def _build_doctor_text(
    profile: dict,
    symptoms: str | None,
    repository: MongoRepository,
) -> tuple[str, str]:
    patient_id = profile["id"]
    meds = await repository.find_patient_records(
        "medicines",
        patient_id,
        filters={"active": True},
        projection={"name": 1, "dose": 1},
    )
    labs = await repository.find_patient_records(
        "lab_values",
        patient_id,
        projection={"analyte": 1, "value": 1, "unit": 1},
        sort=[("measured_at", -1)],
        limit=8,
    )
    meds_line = ", ".join(f"{medicine['name']} {medicine['dose']}" for medicine in meds) or "n/a"
    labs_line = ", ".join(
        f"{lab['analyte']}={lab['value']}{lab.get('unit') or ''}" for lab in labs
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
async def doctor_whatsapp(
    body: DoctorPayload,
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    wa_url, text = await _build_doctor_text(profile, body.symptoms, repository)
    return {"wa_url": wa_url, "text": text}


@router.post("/sos/trigger")
async def sos_trigger(
    body: SosBody,
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    patient_id = profile["id"]
    contacts = await repository.find_patient_records(
        "emergency_contacts",
        patient_id,
        projection={"full_name": 1, "phone": 1, "relation": 1, "sort_order": 1},
        sort=[("sort_order", 1)],
    )
    if not contacts:
        raise HTTPException(400, "Add at least one emergency contact first")

    maps = (
        f"https://maps.google.com/?q={body.lat},{body.lng}"
        if body.lat is not None and body.lng is not None
        else ""
    )
    message = (
        f"SOS from {profile.get('full_name')} (TransplantCare). "
        f"Possible emergency. Location: {maps or 'unavailable'}. "
        f"Symptoms: {body.symptoms or 'n/a'}. Please call them now."
    )
    event = await repository.insert_patient_record(
        "sos_events",
        patient_id,
        {
            "lat": body.lat,
            "lng": body.lng,
            "symptoms": body.symptoms,
            "status": "pending",
        },
    )

    sms_results = await asyncio.gather(
        *(_send_sms_safely(contact["phone"], message) for contact in contacts)
    )
    delivered_count = sum(1 for result in sms_results if result.get("ok"))
    delivery_status = (
        "sent"
        if delivered_count == len(sms_results)
        else "partial"
        if delivered_count
        else "failed"
    )
    if any(result.get("dry_run") for result in sms_results):
        delivery_status = "dry_run"
    await repository.update_patient_record(
        "sos_events",
        patient_id,
        event["id"],
        {"status": delivery_status},
    )
    event["status"] = delivery_status
    whatsapp_urls = []
    for contact in contacts:
        phone = contact["phone"].lstrip("+").replace(" ", "")
        whatsapp_urls.append(
            {
                "name": contact["full_name"],
                "wa_url": f"https://wa.me/{phone}?text={quote(message)}",
            }
        )

    first = contacts[0]
    return {
        "event": [event],
        "sms_results": sms_results,
        "delivery_status": delivery_status,
        "whatsapp_urls": whatsapp_urls,
        "dial_uri": f"tel:{first['phone']}",
        "first_contact": first,
        "disclaimer": "If this is life-threatening, call local emergency services immediately.",
    }


@router.post("/sos/cancel")
async def sos_cancel(
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    latest = await repository.find_one_patient_record(
        "sos_events",
        profile["id"],
        sort=[("created_at", -1)],
    )
    if latest:
        await repository.update_patient_record(
            "sos_events",
            profile["id"],
            latest["id"],
            {"status": "cancelled"},
        )
    return {"ok": True}
