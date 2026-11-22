from datetime import date, datetime, time, timezone
from decimal import Decimal
from uuid import uuid4

from mongomock import MongoClient

from scripts.migrate_supabase_to_mongo import (
    _report_storage_path,
    _upsert_collection,
    transform_auth_user,
    transform_public_row,
)


def test_transform_preserves_legacy_fields_and_normalizes_postgres_values():
    record_id = uuid4()
    patient_id = uuid4()
    migrated = transform_public_row(
        "medicines",
        {
            "id": record_id,
            "patient_id": patient_id,
            "name": "Medicine",
            "dose": "5 mg",
            "schedule_times": [time(8, 30), time(20, 0)],
            "active": True,
            "notes": None,
            "created_at": datetime(2025, 1, 1, 12, tzinfo=timezone.utc),
        },
    )

    assert migrated["_id"] == str(record_id)
    assert migrated["patient_id"] == str(patient_id)
    assert migrated["schedule_times"] == ["08:30", "20:00"]
    assert migrated["created_at"].tzinfo == timezone.utc


def test_transform_maps_date_and_numeric_fields_to_mongo_document():
    record_id = uuid4()
    patient_id = uuid4()
    migrated = transform_public_row(
        "lab_values",
        {
            "id": record_id,
            "patient_id": patient_id,
            "report_id": uuid4(),
            "analyte": "creatinine",
            "value": Decimal("1.25"),
            "unit": "mg/dL",
            "ref_low": Decimal("0.70"),
            "ref_high": Decimal("1.30"),
            "out_of_range": False,
            "measured_at": date(2025, 2, 3),
        },
    )

    assert migrated["_id"] == str(record_id)
    assert migrated["value"] == 1.25
    assert migrated["measured_at"] == datetime(
        2025, 2, 3, tzinfo=timezone.utc
    )


def test_transform_handles_tables_without_id_and_appointment_rename():
    patient_id = uuid4()
    limits = transform_public_row(
        "dietary_limits",
        {"patient_id": patient_id, "potassium_mg": Decimal("250")},
    )
    appointment = transform_public_row(
        "appointments",
        {
            "id": uuid4(),
            "patient_id": patient_id,
            "title": "Checkup",
            "starts_at": datetime(2025, 3, 1, tzinfo=timezone.utc),
            "location": None,
            "notes": None,
            "reminder_minutes": [1440, 60],
        },
    )

    assert limits["_id"] == str(patient_id)
    assert limits["patient_id"] == str(patient_id)
    assert appointment["scheduled_at"] == datetime(
        2025, 3, 1, tzinfo=timezone.utc
    )
    assert "starts_at" not in appointment


def test_auth_user_import_preserves_bcrypt_and_consent_state():
    user_id = uuid4()
    source = {
        "id": user_id,
        "email": "Patient@Example.Test",
        "encrypted_password": "$2b$12$stored-hash",
        "created_at": datetime(2025, 1, 1),
    }

    migrated = transform_auth_user(
        source,
        "patient",
        {
            "accepted": True,
            "policy_version": "v2",
            "accepted_at": datetime(2025, 1, 2),
        },
    )

    assert migrated["_id"] == str(user_id)
    assert migrated["email"] == "patient@example.test"
    assert migrated["password_hash"] == "$2b$12$stored-hash"
    assert migrated["consent_version"] == "v2"
    assert migrated["consent_at"].tzinfo == timezone.utc


def test_report_storage_path_accepts_supabase_urls():
    assert (
        _report_storage_path(
            "https://example.test/storage/v1/object/public/"
            "report-uploads/user-id/report.pdf"
        )
        == "user-id/report.pdf"
    )


def test_collection_upserts_are_idempotent():
    client = MongoClient()
    try:
        row = {
            "_id": "record-1",
            "storage_path": "old/report.pdf",
            "storage_file_id": "gridfs-file-id",
        }
        assert _upsert_collection(client["migration_test"], "lab_reports", [row]) == 1
        assert _upsert_collection(
            client["migration_test"],
            "lab_reports",
            [{"_id": "record-1", "storage_path": "new/report.pdf"}],
        ) == 1
        collection = client["migration_test"]["lab_reports"]
        assert collection.count_documents({}) == 1
        migrated = collection.find_one({"_id": "record-1"})
        assert migrated["storage_path"] == "new/report.pdf"
        assert migrated["storage_file_id"] == "gridfs-file-id"
    finally:
        client.close()
