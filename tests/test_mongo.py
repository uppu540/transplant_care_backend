import asyncio
from datetime import datetime, timedelta, timezone

from app.models import FoodLogDocument
from app.mongo import INDEXES, ensure_indexes


class FakeCollection:
    def __init__(self):
        self.indexes = []

    async def create_index(self, keys, **options):
        self.indexes.append((keys, options))


class FakeDatabase:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


def test_ensure_indexes_creates_required_indexes():
    database = FakeDatabase()

    asyncio.run(ensure_indexes(database))

    expected = {}
    for collection_name, keys, options in INDEXES:
        expected.setdefault(collection_name, []).append((keys, options))

    for collection_name, indexes in expected.items():
        assert database[collection_name].indexes == indexes

    ttl = database["caregiver_links"].indexes[-1][1]
    assert ttl["expireAfterSeconds"] == 0
    assert database["users"].indexes[0][1]["unique"] is True


def test_document_datetimes_are_stored_in_utc():
    local_time = datetime(2026, 11, 22, 12, 0, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    doc = FoodLogDocument(
        patient_id="patient-1",
        description="lunch",
        logged_at=local_time,
    )

    assert doc.logged_at == datetime(2026, 11, 22, 6, 30, tzinfo=timezone.utc)
