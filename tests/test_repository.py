import asyncio
from datetime import datetime, timezone

import pytest

from app.repository import MongoRepository, PATIENT_COLLECTIONS

VALID_RECORDS = {
    "appointments": {"title": "follow-up"},
    "caregiver_links": {"invite_code": "ABCDEFGH", "status": "pending"},
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


class FakeCursor:
    def __init__(self, documents):
        self.documents = documents

    def sort(self, specification):
        return self

    def limit(self, count):
        self.documents = self.documents[:count]
        return self

    async def to_list(self, length=None):
        return self.documents[:length]


class FakeCollection:
    def __init__(self):
        self.documents = []
        self.last_query = None

    def find(self, query, projection=None):
        self.last_query = query
        matching = [
            dict(document)
            for document in self.documents
            if all(document.get(key) == value for key, value in query.items())
        ]
        return FakeCursor(matching)

    async def insert_one(self, document):
        self.documents.append(dict(document))

    async def update_one(self, query, update):
        self.last_query = query
        return None

    async def delete_one(self, query):
        self.last_query = query
        return None


class FakeDatabase:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, name):
        return self.collections.setdefault(name, FakeCollection())


@pytest.mark.parametrize("collection_name", sorted(PATIENT_COLLECTIONS))
def test_patient_reads_cannot_override_owner_filter(collection_name):
    database = FakeDatabase()
    collection = database[collection_name]
    collection.documents = [
        {"_id": "record-a", "patient_id": "patient-a", "active": True},
        {"_id": "record-b", "patient_id": "patient-b", "active": True},
    ]
    repository = MongoRepository(database)

    records = asyncio.run(
        repository.find_patient_records(
            collection_name,
            "patient-a",
            filters={"patient_id": "patient-b", "active": True},
        )
    )

    assert [record["id"] for record in records] == ["record-a"]
    assert collection.last_query == {"active": True, "patient_id": "patient-a"}


@pytest.mark.parametrize("collection_name", sorted(PATIENT_COLLECTIONS))
def test_patient_inserts_force_authenticated_owner(collection_name):
    database = FakeDatabase()
    repository = MongoRepository(database)

    record = asyncio.run(
        repository.insert_patient_record(
            collection_name,
            "patient-a",
            {
                **VALID_RECORDS[collection_name],
                "patient_id": "patient-b",
                "extra_marker": "test",
            },
        )
    )

    stored = database[collection_name].documents[0]
    assert record["patient_id"] == "patient-a"
    assert stored["patient_id"] == "patient-a"
    assert stored["extra_marker"] == "test"


@pytest.mark.parametrize("collection_name", sorted(PATIENT_COLLECTIONS))
def test_patient_updates_and_deletes_include_owner(collection_name):
    database = FakeDatabase()
    repository = MongoRepository(database)

    asyncio.run(
        repository.update_patient_record(
            collection_name,
            "patient-a",
            "record-b",
            {"patient_id": "patient-b", "value": "changed"},
        )
    )
    assert database[collection_name].last_query == {
        "_id": "record-b",
        "patient_id": "patient-a",
    }

    asyncio.run(
        repository.delete_patient_record(
            collection_name,
            "patient-a",
            "record-b",
        )
    )
    assert database[collection_name].last_query == {
        "_id": "record-b",
        "patient_id": "patient-a",
    }


def test_repository_rejects_unscoped_collection_names():
    repository = MongoRepository(FakeDatabase())

    with pytest.raises(ValueError):
        asyncio.run(repository.find_patient_records("users", "patient-a"))
