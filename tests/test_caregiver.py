import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.auth import AuthUser
from app.routers.me import (
    AcceptInviteBody,
    accept_caregiver_invite,
    create_caregiver_invite,
    get_caregiver_patient_view,
    list_caregiver_patients,
)


def test_caregiver_flow_end_to_end(
    mongo_repository,
    profile_factory,
    user_factory,
):
    patient_user_id = "patient-user-1"
    patient_profile = profile_factory(
        patient_user_id,
        full_name="Arjun Patel",
        transplant_date="2024-03-10",
        doctor_name="Sharma",
        doctor_whatsapp="+919876500000",
        role="patient",
    )
    patient_id = patient_profile["id"]
    user_factory(patient_user_id, "patient@example.test")
    asyncio.run(
        mongo_repository.insert_patient_record(
            "medicines",
            patient_id,
            {
                "name": "Tacrolimus",
                "dose": "1.5mg",
                "schedule_times": ["08:00:00", "20:00:00"],
                "active": True,
            },
        )
    )
    asyncio.run(
        mongo_repository.insert_patient_record(
            "dietary_limits",
            patient_id,
            {"potassium_mg": 2500, "sodium_mg": 2000, "protein_g": 65},
        )
    )

    invite = asyncio.run(
        create_caregiver_invite(
            user=AuthUser(user_id=patient_user_id),
            repository=mongo_repository,
        )
    )
    assert invite["ok"] is True
    code = invite["invite_code"]
    assert len(code) == 8

    caregiver_user_id = "caregiver-user-2"
    profile_factory(
        caregiver_user_id,
        full_name="Priya Patel",
        role="caregiver",
    )
    user_factory(caregiver_user_id, "caregiver@example.test", role="caregiver")
    accepted = asyncio.run(
        accept_caregiver_invite(
            AcceptInviteBody(invite_code=code),
            user=AuthUser(user_id=caregiver_user_id, role="caregiver"),
            repository=mongo_repository,
        )
    )
    assert accepted["ok"] is True
    assert accepted["patient_id"] == patient_id

    patients = asyncio.run(
        list_caregiver_patients(
            user=AuthUser(user_id=caregiver_user_id, role="caregiver"),
            repository=mongo_repository,
        )
    )
    assert len(patients["patients"]) == 1
    assert patients["patients"][0]["full_name"] == "Arjun Patel"
    assert "doctor_whatsapp" not in patients["patients"][0]

    view = asyncio.run(
        get_caregiver_patient_view(
            patient_id,
            user=AuthUser(user_id=caregiver_user_id, role="caregiver"),
            repository=mongo_repository,
        )
    )
    assert view["read_only"] is True
    assert view["patient"]["full_name"] == "Arjun Patel"
    assert len(view["medicines"]) == 1
    assert view["budget"]["limits_set"] is True
    assert view["budget"]["limits"]["potassium_mg"] == 2500

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            get_caregiver_patient_view(
                patient_id,
                user=AuthUser(user_id="random-stranger", role="caregiver"),
                repository=mongo_repository,
            )
        )
    assert exc.value.status_code == 403


def test_expired_and_reused_invite_codes_are_rejected(
    mongo_repository,
    mongo_database,
    profile_factory,
    user_factory,
):
    patient_id = "invite-patient"
    profile_factory(patient_id)
    user_factory(patient_id, "invite-patient@example.test")
    caregiver_id = "invite-caregiver"
    profile_factory(caregiver_id, role="caregiver")
    user_factory(caregiver_id, "invite-caregiver@example.test", role="caregiver")

    expired_invite = asyncio.run(
        create_caregiver_invite(
            user=AuthUser(user_id=patient_id),
            repository=mongo_repository,
        )
    )
    asyncio.run(
        mongo_database["caregiver_links"].update_one(
            {"invite_code": expired_invite["invite_code"]},
            {"$set": {"expires_at": datetime.now(timezone.utc) - timedelta(seconds=1)}},
        )
    )
    with pytest.raises(HTTPException) as expired_error:
        asyncio.run(
            accept_caregiver_invite(
                AcceptInviteBody(invite_code=expired_invite["invite_code"]),
                user=AuthUser(user_id=caregiver_id, role="caregiver"),
                repository=mongo_repository,
            )
        )
    assert expired_error.value.status_code == 404

    valid_invite = asyncio.run(
        create_caregiver_invite(
            user=AuthUser(user_id=patient_id),
            repository=mongo_repository,
        )
    )
    accept_body = AcceptInviteBody(invite_code=valid_invite["invite_code"])
    accepted = asyncio.run(
        accept_caregiver_invite(
            accept_body,
            user=AuthUser(user_id=caregiver_id, role="caregiver"),
            repository=mongo_repository,
        )
    )
    assert accepted["ok"] is True

    with pytest.raises(HTTPException) as reused_error:
        asyncio.run(
            accept_caregiver_invite(
                accept_body,
                user=AuthUser(user_id=caregiver_id, role="caregiver"),
                repository=mongo_repository,
            )
        )
    assert reused_error.value.status_code == 404
