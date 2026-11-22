from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from pymongo import ASCENDING

from app.config import get_settings

_client: AsyncIOMotorClient | None = None

INDEXES: tuple[tuple[str, list[tuple[str, int]], dict[str, Any]], ...] = (
    ("users", [("email", ASCENDING)], {"unique": True, "name": "users_email_unique"}),
    (
        "profiles",
        [("user_id", ASCENDING)],
        {"unique": True, "name": "profiles_user_id_unique"},
    ),
    (
        "medicines",
        [("patient_id", ASCENDING), ("schedule_times", ASCENDING)],
        {"name": "medicines_patient_schedule"},
    ),
    (
        "dose_logs",
        [("patient_id", ASCENDING), ("scheduled_at", ASCENDING)],
        {"name": "dose_logs_patient_scheduled"},
    ),
    (
        "food_logs",
        [("patient_id", ASCENDING), ("logged_at", ASCENDING)],
        {"name": "food_logs_patient_logged"},
    ),
    (
        "lab_values",
        [
            ("patient_id", ASCENDING),
            ("measured_at", ASCENDING),
            ("analyte", ASCENDING),
        ],
        {"name": "lab_values_patient_measured_analyte"},
    ),
    (
        "caregiver_links",
        [("invite_code", ASCENDING)],
        {"unique": True, "name": "caregiver_invite_code_unique"},
    ),
    (
        "caregiver_links",
        [("expires_at", ASCENDING)],
        {"expireAfterSeconds": 0, "name": "caregiver_invite_expiry_ttl"},
    ),
    (
        "rate_limits",
        [("expires_at", ASCENDING)],
        {"expireAfterSeconds": 0, "name": "rate_limit_expiry_ttl"},
    ),
    (
        "refresh_tokens",
        [("expires_at", ASCENDING)],
        {"expireAfterSeconds": 0, "name": "refresh_token_expiry_ttl"},
    ),
)


def get_mongo_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        settings = get_settings()
        if not settings.mongodb_uri:
            raise RuntimeError("MONGODB_URI is not configured")
        _client = AsyncIOMotorClient(settings.mongodb_uri, tz_aware=True)
    return _client


def get_mongo_database() -> AsyncIOMotorDatabase:
    settings = get_settings()
    if not settings.mongodb_database:
        raise RuntimeError("MONGODB_DATABASE is not configured")
    return get_mongo_client()[settings.mongodb_database]


async def ensure_indexes(database: Any) -> None:
    for collection_name, keys, options in INDEXES:
        await database[collection_name].create_index(keys, **options)


async def connect_mongo() -> AsyncIOMotorDatabase:
    database = get_mongo_database()
    await database.command("ping")
    await ensure_indexes(database)
    return database


async def close_mongo() -> None:
    global _client
    if _client is not None:
        _client.close()
        _client = None
