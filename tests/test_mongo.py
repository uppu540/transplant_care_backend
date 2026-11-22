import asyncio
from datetime import datetime, timedelta, timezone

from app.models import FoodLogDocument
from app.mongo import ensure_indexes


def test_required_indexes_are_created_in_mongo_database(mongo_database):
    asyncio.run(ensure_indexes(mongo_database))
    users = asyncio.run(mongo_database["users"].index_information())
    invites = asyncio.run(mongo_database["caregiver_links"].index_information())
    refresh_tokens = asyncio.run(
        mongo_database["refresh_tokens"].index_information()
    )

    assert users["users_email_unique"]["unique"] is True
    assert invites["caregiver_invite_code_unique"]["unique"] is True
    assert invites["caregiver_invite_expiry_ttl"]["expireAfterSeconds"] == 0
    assert refresh_tokens["refresh_token_expiry_ttl"]["expireAfterSeconds"] == 0


def test_document_datetimes_are_stored_in_utc():
    local_time = datetime(
        2026,
        11,
        22,
        12,
        0,
        tzinfo=timezone(timedelta(hours=5, minutes=30)),
    )
    doc = FoodLogDocument(
        patient_id="patient-1",
        description="lunch",
        logged_at=local_time,
    )

    assert doc.logged_at == datetime(2026, 11, 22, 6, 30, tzinfo=timezone.utc)
