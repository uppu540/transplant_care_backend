import asyncio

import pytest
from fastapi import HTTPException

from app.auth import AuthUser
from app.routers.emergency import (
    SosBody,
    _build_doctor_text,
    sos_cancel,
    sos_trigger,
)


def test_doctor_whatsapp_payload_building(mongo_repository, profile_factory):
    profile = profile_factory(
        "user-doctor-test",
        full_name="Ravi Kumar",
        transplant_date="2025-06-15",
        doctor_name="Reddy",
        doctor_whatsapp="+919876543210",
    )
    patient_id = profile["id"]
    asyncio.run(
        mongo_repository.insert_patient_record(
            "medicines",
            patient_id,
            {"name": "Tacrolimus", "dose": "2mg", "active": True},
        )
    )
    asyncio.run(
        mongo_repository.insert_patient_record(
            "lab_values",
            patient_id,
            {
                "report_id": "report-1",
                "analyte": "creatinine",
                "value": 1.1,
                "unit": "mg/dL",
                "measured_at": "2026-11-20",
            },
        )
    )

    wa_url, text = asyncio.run(
        _build_doctor_text(profile, "Mild ankle swelling", mongo_repository)
    )
    assert "https://wa.me/919876543210?text=" in wa_url
    assert "Dr. Reddy" in text
    assert "Ravi Kumar" in text
    assert "Mild ankle swelling" in text
    assert "creatinine=1.1mg/dL" in text
    assert "Tacrolimus 2mg" in text
    assert "TransplantCare" in text


def test_sos_trigger_no_contacts_raises(mongo_repository, profile_factory):
    user_id = "user-no-contacts"
    profile_factory(user_id, full_name="No Contact Patient")

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            sos_trigger(
                SosBody(lat=17.385, lng=78.486),
                user=AuthUser(user_id=user_id),
                repository=mongo_repository,
            )
        )
    assert "Add at least one emergency contact" in str(exc.value)


def test_sos_trigger_with_contacts_and_cancel(
    mongo_repository,
    profile_factory,
    monkeypatch,
):
    from app.routers import emergency

    monkeypatch.setattr(
        emergency,
        "send_sms",
        lambda phone, message: {"ok": True, "dry_run": True},
    )
    user_id = "user-sos-patient"
    profile = profile_factory(user_id, full_name="Ananya Sharma")
    patient_id = profile["id"]

    for contact in (
        {
            "full_name": "Caregiver Mom",
            "phone": "+919811111111",
            "relation": "Mother",
            "sort_order": 0,
        },
        {
            "full_name": "Brother",
            "phone": "+919822222222",
            "relation": "Brother",
            "sort_order": 1,
        },
    ):
        asyncio.run(
            mongo_repository.insert_patient_record(
                "emergency_contacts",
                patient_id,
                contact,
            )
        )

    user = AuthUser(user_id=user_id)
    result = asyncio.run(
        sos_trigger(
            SosBody(lat=17.44, lng=78.38, symptoms="Dizziness and chest pain"),
            user=user,
            repository=mongo_repository,
        )
    )
    assert result["dial_uri"] == "tel:+919811111111"
    assert result["delivery_status"] == "dry_run"
    assert result["event"][0]["status"] == "dry_run"
    assert result["first_contact"]["full_name"] == "Caregiver Mom"
    assert len(result["sms_results"]) == 2
    assert len(result["whatsapp_urls"]) == 2
    assert "maps.google.com" in result["whatsapp_urls"][0]["wa_url"]

    cancel_result = asyncio.run(
        sos_cancel(user=user, repository=mongo_repository)
    )
    assert cancel_result["ok"] is True
    events = asyncio.run(
        mongo_repository.find_patient_records(
            "sos_events",
            patient_id,
            sort=[("created_at", -1)],
        )
    )
    assert len(events) == 1
    assert events[0]["status"] == "cancelled"


def test_sos_reports_failed_delivery(
    mongo_repository,
    profile_factory,
    monkeypatch,
):
    from app.routers import emergency

    monkeypatch.setattr(
        emergency,
        "send_sms",
        lambda phone, message: {"ok": False, "error": "provider unavailable"},
    )
    user_id = "user-sos-failed"
    profile = profile_factory(user_id, full_name="Ananya Sharma")
    asyncio.run(
        mongo_repository.insert_patient_record(
            "emergency_contacts",
            profile["id"],
            {
                "full_name": "Caregiver Mom",
                "phone": "+919811111111",
                "relation": "Mother",
                "sort_order": 0,
            },
        )
    )

    result = asyncio.run(
        sos_trigger(
            SosBody(symptoms="Chest pain"),
            user=AuthUser(user_id=user_id),
            repository=mongo_repository,
        )
    )

    assert result["delivery_status"] == "failed"
    assert result["sms_results"] == [
        {"ok": False, "error": "provider unavailable"}
    ]
