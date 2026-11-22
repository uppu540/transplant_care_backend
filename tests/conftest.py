import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from mongomock_motor import AsyncMongoMockClient

from app.mongo import ensure_indexes
from app.repository import MongoRepository


@pytest.fixture
def mongo_database():
    client = AsyncMongoMockClient(tz_aware=True)
    database = client["transplantcare_test"]
    asyncio.run(ensure_indexes(database))
    yield database
    client.close()


@pytest.fixture
def mongo_repository(mongo_database):
    return MongoRepository(mongo_database)


@pytest.fixture
def profile_factory(mongo_repository):
    def create_profile(user_id: str, **fields):
        profile = {
            "_id": str(uuid4()),
            "user_id": user_id,
            "full_name": fields.pop("full_name", "Test Patient"),
            **fields,
        }
        asyncio.run(mongo_repository.create_profile(profile))
        return asyncio.run(mongo_repository.get_profile_by_user_id(user_id))

    return create_profile


@pytest.fixture
def user_factory(mongo_repository):
    def create_user(user_id: str, email: str, role: str = "patient"):
        user = {
            "_id": user_id,
            "email": email,
            "password_hash": "test-password-hash",
            "role": role,
            "consent_version": "test-v1",
            "consent_at": datetime.now(timezone.utc),
        }
        asyncio.run(mongo_repository.create_user(user))
        return user

    return create_user
