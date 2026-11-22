import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pymongo.errors import DuplicateKeyError

from app.auth import AuthUser, create_access_token, get_current_user
from app.config import Settings
from app.rate_limit import rate_limit
from app.routers import auth as auth_routes


class FakeCollection:
    def __init__(self, record=None):
        self.record = record
        self.count = 0

    async def find_one(self, query, projection=None):
        if self.record and self.record.get("_id") == query["_id"]:
            return self.record
        return None

    async def find_one_and_update(
        self,
        query,
        update,
        upsert=False,
        return_document=None,
    ):
        self.count += update["$inc"]["count"]
        return {"_id": query["_id"], "count": self.count}


class FakeDatabase:
    def __init__(self, user=None):
        self.collections = {
            "users": FakeCollection(user),
            "rate_limits": FakeCollection(),
        }

    def __getitem__(self, collection_name):
        return self.collections[collection_name]


class MemoryCollection:
    def __init__(self):
        self.records = []

    async def insert_one(self, document):
        if any(record.get("_id") == document.get("_id") for record in self.records):
            raise DuplicateKeyError("duplicate _id")
        if document.get("email") and any(
            record.get("email") == document["email"] for record in self.records
        ):
            raise DuplicateKeyError("duplicate email")
        self.records.append(dict(document))
        return SimpleNamespace(inserted_id=document["_id"])

    async def find_one(self, query, projection=None):
        return next((dict(record) for record in self.records if self._matches(record, query)), None)

    async def update_one(self, query, update):
        record = next((record for record in self.records if self._matches(record, query)), None)
        if record is None:
            return SimpleNamespace(matched_count=0, modified_count=0)
        record.update(update.get("$set", {}))
        return SimpleNamespace(matched_count=1, modified_count=1)

    async def find_one_and_update(
        self,
        query,
        update,
        upsert=False,
        return_document=None,
    ):
        record = next((record for record in self.records if self._matches(record, query)), None)
        if record is None:
            if upsert:
                record = {"_id": query["_id"], "count": 0}
                self.records.append(record)
            else:
                return None
        before = dict(record)
        record.update(update.get("$set", {}))
        for field, amount in update.get("$inc", {}).items():
            record[field] = record.get(field, 0) + amount
        record.update(update.get("$setOnInsert", {}) if before.get("count") == 0 else {})
        return dict(record) if return_document else before

    @staticmethod
    def _matches(record, query):
        for key, expected in query.items():
            actual = record.get(key)
            if isinstance(expected, dict) and "$gt" in expected:
                if actual is None or actual <= expected["$gt"]:
                    return False
            elif actual != expected:
                return False
        return True


class MemoryDatabase:
    def __init__(self):
        self.collections = {}

    def __getitem__(self, collection_name):
        return self.collections.setdefault(collection_name, MemoryCollection())


def _settings():
    return Settings(
        _env_file=None,
        jwt_secret="unit-test-jwt-secret-with-at-least-32-bytes",
        jwt_issuer="transplantcare",
        access_token_minutes=15,
    )


def _credentials(settings):
    token = create_access_token(
        user_id="user-1",
        email="patient@example.test",
        role="patient",
        settings=settings,
    )
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def test_access_token_has_short_lived_auth_claims():
    settings = _settings()
    token = create_access_token(
        user_id="user-1",
        email="patient@example.test",
        role="patient",
        settings=settings,
    )

    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        audience="transplantcare",
        issuer=settings.jwt_issuer,
    )

    assert payload["sub"] == "user-1"
    assert payload["role"] == "patient"
    assert payload["type"] == "access"
    assert payload["exp"] - payload["iat"] == 15 * 60


def test_current_user_blocks_data_access_without_consent():
    settings = _settings()
    request = SimpleNamespace(url=SimpleNamespace(path="/me/summary"))
    database = FakeDatabase({"_id": "user-1"})

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            get_current_user(
                request=request,
                credentials=_credentials(settings),
                settings=settings,
                database=database,
            )
        )

    assert exc.value.status_code == 403
    assert "consent" in exc.value.detail.lower()


def test_current_user_allows_consented_user_and_consent_route():
    settings = _settings()
    credentials = _credentials(settings)
    consented_user = {
        "_id": "user-1",
        "consent_version": "v1",
        "consent_at": datetime.now(timezone.utc),
        "role": "patient",
    }

    user = asyncio.run(
        get_current_user(
            request=SimpleNamespace(url=SimpleNamespace(path="/me/summary")),
            credentials=credentials,
            settings=settings,
            database=FakeDatabase(consented_user),
        )
    )
    assert user == AuthUser(user_id="user-1", email="patient@example.test", role="patient")

    consent_route_user = asyncio.run(
        get_current_user(
            request=SimpleNamespace(url=SimpleNamespace(path="/me/consent")),
            credentials=credentials,
            settings=settings,
            database=FakeDatabase({"_id": "user-1"}),
        )
    )
    assert consent_route_user.user_id == "user-1"


def test_test_tokens_are_development_only():
    credentials = HTTPAuthorizationCredentials(
        scheme="Bearer",
        credentials="test:user-1:patient@example.test",
    )
    request = SimpleNamespace(url=SimpleNamespace(path="/me/consent"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            get_current_user(
                request=request,
                credentials=credentials,
                settings=_settings(),
                database=FakeDatabase(),
            )
        )
    assert exc.value.status_code == 401

    development_settings = _settings()
    development_settings.app_env = "development"
    user = asyncio.run(
        get_current_user(
            request=request,
            credentials=credentials,
            settings=development_settings,
            database=FakeDatabase(),
        )
    )
    assert user.user_id == "user-1"


def test_rate_limit_rejects_requests_over_limit():
    limiter = rate_limit("login", max_requests=1, window_seconds=900)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))
    database = FakeDatabase()

    asyncio.run(limiter(request=request, database=database))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(limiter(request=request, database=database))

    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_register_login_and_single_use_refresh_token(monkeypatch):
    settings = _settings()
    monkeypatch.setattr(auth_routes, "get_settings", lambda: settings)
    database = MemoryDatabase()

    registered = asyncio.run(
        auth_routes.register(
            auth_routes.RegisterBody(
                email="Patient@Example.Test",
                password="a-strong-password",
                full_name="Patient One",
            ),
            database=database,
        )
    )
    assert registered["role"] == "patient"
    assert registered["refresh_token"]

    user = database["users"].records[0]
    assert user["email"] == "patient@example.test"
    assert user["password_hash"] != "a-strong-password"

    logged_in = asyncio.run(
        auth_routes.login(
            auth_routes.LoginBody(
                email="patient@example.test",
                password="a-strong-password",
            ),
            database=database,
        )
    )
    rotated = asyncio.run(
        auth_routes.refresh(
            auth_routes.RefreshBody(refresh_token=logged_in["refresh_token"]),
            database=database,
        )
    )
    assert rotated["refresh_token"] != logged_in["refresh_token"]

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            auth_routes.refresh(
                auth_routes.RefreshBody(refresh_token=logged_in["refresh_token"]),
                database=database,
            )
        )
    assert exc.value.status_code == 401
