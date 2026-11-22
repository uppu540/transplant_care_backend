import asyncio

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.auth import AuthUser
from app.routers.food import delete_food_log, list_food_logs
from app.routers.me import (
    DietaryLimitsBody,
    EmergencyContactInput,
    EmergencyContactsBody,
    ProfileUpdateBody,
    account_status,
    get_dietary_limits,
    list_emergency_contacts,
    replace_emergency_contacts,
    set_dietary_limits,
    update_profile,
)
from app.routers.medicines import (
    MedicineBody,
    MedicineUpdateBody,
    create_medicine,
    deactivate_medicine,
    list_medicines,
    update_medicine,
)


def test_status_and_profile_update(mongo_repository, profile_factory, user_factory):
    profile = profile_factory(
        "profile-user",
        full_name="Patient",
        onboarding_complete=False,
    )
    user_factory("profile-user", "profile@example.test")
    user = AuthUser(user_id="profile-user", has_consent=True)

    status = asyncio.run(account_status(user=user, repository=mongo_repository))
    assert status == {
        "role": "patient",
        "has_consent": True,
        "onboarding_complete": False,
    }

    updated = asyncio.run(
        update_profile(
            ProfileUpdateBody(
                full_name="Patient Example",
                doctor_name="Dr Example",
                locale="en",
            ),
            user=user,
            repository=mongo_repository,
        )
    )
    assert updated["profile"]["full_name"] == "Patient Example"
    assert updated["profile"]["doctor_name"] == "Dr Example"
    assert updated["profile"]["id"] == profile["id"]


def test_emergency_contacts_replace_and_are_user_scoped(
    mongo_repository,
    profile_factory,
):
    patient = profile_factory("contact-owner")
    profile_factory("contact-other")
    user = AuthUser(user_id="contact-owner")

    asyncio.run(
        mongo_repository.insert_patient_record(
            "emergency_contacts",
            patient["id"],
            {"full_name": "Old Contact", "phone": "+10000000000", "sort_order": 0},
        )
    )
    result = asyncio.run(
        replace_emergency_contacts(
            EmergencyContactsBody(
                contacts=[
                    EmergencyContactInput(
                        full_name="Primary Contact",
                        phone="+19999999999",
                        relation="Sibling",
                    ),
                    EmergencyContactInput(
                        full_name="Secondary Contact",
                        phone="+18888888888",
                    ),
                ]
            ),
            user=user,
            repository=mongo_repository,
        )
    )
    listed = asyncio.run(
        list_emergency_contacts(user=user, repository=mongo_repository)
    )

    assert [contact["full_name"] for contact in result["contacts"]] == [
        "Primary Contact",
        "Secondary Contact",
    ]
    assert [contact["sort_order"] for contact in listed["contacts"]] == [0, 1]
    assert all(contact["patient_id"] == patient["id"] for contact in listed["contacts"])


def test_emergency_contact_limit_is_validated():
    with pytest.raises(ValidationError):
        EmergencyContactsBody(
            contacts=[
                EmergencyContactInput(full_name=f"Contact {index}", phone="+10000000000")
                for index in range(6)
            ]
        )


def test_dietary_limits_are_upserted_per_patient(
    mongo_repository,
    profile_factory,
):
    profile = profile_factory("limits-owner")
    user = AuthUser(user_id="limits-owner")

    first = asyncio.run(
        set_dietary_limits(
            DietaryLimitsBody(potassium_mg=1800, sodium_mg=2000),
            user=user,
            repository=mongo_repository,
        )
    )
    second = asyncio.run(
        set_dietary_limits(
            DietaryLimitsBody(potassium_mg=1600, sodium_mg=1900),
            user=user,
            repository=mongo_repository,
        )
    )
    loaded = asyncio.run(
        get_dietary_limits(user=user, repository=mongo_repository)
    )

    assert first["limits"]["patient_id"] == profile["id"]
    assert second["limits"]["id"] == first["limits"]["id"]
    assert loaded["limits"]["potassium_mg"] == 1600
    records = asyncio.run(
        mongo_repository.find_patient_records("dietary_limits", profile["id"])
    )
    assert len(records) == 1


def test_medicine_crud_uses_authenticated_profile(
    mongo_repository,
    profile_factory,
):
    profile = profile_factory("medicine-owner")
    profile_factory("medicine-other")
    user = AuthUser(user_id="medicine-owner")

    created = asyncio.run(
        create_medicine(
            MedicineBody(
                name=" Tacrolimus ",
                dose=" 1 mg ",
                schedule_times=["08:00", "20:00:00"],
            ),
            user=user,
            repository=mongo_repository,
        )
    )["medicine"]
    assert created["patient_id"] == profile["id"]
    assert created["schedule_times"] == ["08:00:00", "20:00:00"]

    updated = asyncio.run(
        update_medicine(
            created["id"],
            MedicineUpdateBody(notes="Take with water"),
            user=user,
            repository=mongo_repository,
        )
    )["medicine"]
    assert updated["notes"] == "Take with water"

    listed = asyncio.run(list_medicines(user=user, repository=mongo_repository))
    assert [medicine["id"] for medicine in listed["medicines"]] == [created["id"]]

    result = asyncio.run(
        deactivate_medicine(
            created["id"],
            user=user,
            repository=mongo_repository,
        )
    )
    assert result["active"] is False
    assert asyncio.run(list_medicines(user=user, repository=mongo_repository))[
        "medicines"
    ] == []

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            update_medicine(
                created["id"],
                MedicineUpdateBody(active=True),
                user=AuthUser(user_id="medicine-other"),
                repository=mongo_repository,
            )
        )
    assert error.value.status_code == 404


def test_food_log_history_and_delete_are_patient_scoped(
    mongo_repository,
    profile_factory,
):
    owner = profile_factory("food-owner")
    other = profile_factory("food-other")
    log = asyncio.run(
        mongo_repository.insert_patient_record(
            "food_logs",
            owner["id"],
            {"description": "Lunch"},
        )
    )
    other_log = asyncio.run(
        mongo_repository.insert_patient_record(
            "food_logs",
            other["id"],
            {"description": "Other patient lunch"},
        )
    )
    user = AuthUser(user_id="food-owner")

    history = asyncio.run(
        list_food_logs(day=None, limit=100, user=user, repository=mongo_repository)
    )
    assert [row["id"] for row in history["logs"]] == [log["id"]]

    with pytest.raises(HTTPException) as error:
        asyncio.run(
            delete_food_log(
                other_log["id"],
                user=user,
                repository=mongo_repository,
            )
        )
    assert error.value.status_code == 404
    assert asyncio.run(
        delete_food_log(log["id"], user=user, repository=mongo_repository)
    )["ok"]

