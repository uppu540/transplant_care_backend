import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.repository import MongoRepository, PATIENT_COLLECTIONS


def _record_for(collection_name: str, patient_id: str) -> dict:
    records = {
        "appointments": {"title": "follow-up"},
        "caregiver_links": {
            "invite_code": uuid4().hex[:8].upper(),
            "status": "pending",
        },
        "chat_messages": {"role": "user", "content": "test"},
        "dietary_limits": {},
        "dose_logs": {
            "medicine_id": "medicine-1",
            "scheduled_at": datetime.now(timezone.utc),
            "status": "taken",
        },
        "emergency_contacts": {"full_name": "Caregiver", "phone": "+10000000000"},
        "food_logs": {"description": "meal"},
        "lab_reports": {
            "collected_at": datetime.now(timezone.utc),
            "storage_path": "private/report",
            "extracted_at": datetime.now(timezone.utc),
        },
        "lab_values": {
            "report_id": "report-1",
            "analyte": "creatinine",
            "value": 1.0,
            "measured_at": datetime.now(timezone.utc),
        },
        "medicines": {"name": "Medicine", "dose": "1mg"},
        "sos_events": {"status": "sent"},
    }
    return {
        **records[collection_name],
        "patient_id": patient_id,
        "scope_marker": "test",
    }


@pytest.mark.parametrize("collection_name", sorted(PATIENT_COLLECTIONS))
def test_patient_a_cannot_read_patient_b_records(
    mongo_repository,
    collection_name,
):
    patient_a = "patient-a"
    patient_b = "patient-b"
    patient_a_record = asyncio.run(
        mongo_repository.insert_patient_record(
            collection_name,
            patient_a,
            _record_for(collection_name, patient_b),
        )
    )
    patient_b_record = asyncio.run(
        mongo_repository.insert_patient_record(
            collection_name,
            patient_b,
            _record_for(collection_name, patient_b),
        )
    )

    records = asyncio.run(
        mongo_repository.find_patient_records(
            collection_name,
            patient_a,
            filters={"patient_id": patient_b, "scope_marker": "test"},
        )
    )
    single_record = asyncio.run(
        mongo_repository.find_one_patient_record(
            collection_name,
            patient_a,
            filters={"_id": patient_b_record["id"]},
        )
    )

    assert [record["id"] for record in records] == [patient_a_record["id"]]
    assert patient_b_record["id"] not in [record["id"] for record in records]
    assert single_record is None


@pytest.mark.parametrize("collection_name", sorted(PATIENT_COLLECTIONS))
def test_patient_a_cannot_update_or_delete_patient_b_records(
    mongo_repository,
    collection_name,
):
    patient_a = "patient-a"
    patient_b = "patient-b"
    asyncio.run(
        mongo_repository.insert_patient_record(
            collection_name,
            patient_a,
            _record_for(collection_name, patient_a),
        )
    )
    patient_b_record = asyncio.run(
        mongo_repository.insert_patient_record(
            collection_name,
            patient_b,
            _record_for(collection_name, patient_b),
        )
    )

    update_result = asyncio.run(
        mongo_repository.update_patient_record(
            collection_name,
            patient_a,
            patient_b_record["id"],
            {"scope_marker": "modified"},
        )
    )
    delete_result = asyncio.run(
        mongo_repository.delete_patient_record(
            collection_name,
            patient_a,
            patient_b_record["id"],
        )
    )
    remains = asyncio.run(
        mongo_repository.find_one_patient_record(
            collection_name,
            patient_b,
            filters={"_id": patient_b_record["id"]},
        )
    )

    assert update_result.matched_count == 0
    assert delete_result.deleted_count == 0
    assert remains["scope_marker"] == "test"


@pytest.mark.parametrize("collection_name", sorted(PATIENT_COLLECTIONS))
def test_patient_inserts_force_authenticated_owner(mongo_repository, collection_name):
    record = asyncio.run(
        mongo_repository.insert_patient_record(
            collection_name,
            "patient-a",
            _record_for(collection_name, "patient-b"),
        )
    )
    stored = asyncio.run(
        mongo_repository.find_one_patient_record(
            collection_name,
            "patient-a",
            filters={"_id": record["id"]},
        )
    )

    assert record["patient_id"] == "patient-a"
    assert stored["patient_id"] == "patient-a"


def test_repository_rejects_unscoped_collection_names(mongo_repository):
    with pytest.raises(ValueError):
        asyncio.run(mongo_repository.find_patient_records("users", "patient-a"))
