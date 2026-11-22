import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import bcrypt
import jwt
import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.auth import AuthUser, create_access_token, get_current_user
from app.config import Settings
from app.rate_limit import rate_limit
from app.routers import auth as auth_routes


def _settings():
    return Settings(
        _env_file=None,
        jwt_secret="unit-test-jwt-secret-with-at-least-32-bytes",
        jwt_issuer="transplantcare",
        access_token_minutes=15,
    )


def _credentials(settings, user_id="user-1", role="patient"):
    token = create_access_token(
        user_id=user_id,
        email="patient@example.test",
        role=role,
        settings=settings,
    )
    return HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)


def _create_user(repository, user_id="user-1", role="patient", consented=False):
    user = {
        "_id": user_id,
        "email": f"{user_id}@example.test",
        "password_hash": "test-password-hash",
        "role": role,
        "consent_version": "v1" if consented else None,
        "consent_at": datetime.now(timezone.utc) if consented else None,
    }
    asyncio.run(repository.create_user(user))
    return user


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


def test_current_user_blocks_data_access_without_consent(mongo_repository):
    settings = _settings()
    _create_user(mongo_repository)

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            get_current_user(
                request=SimpleNamespace(url=SimpleNamespace(path="/me/summary")),
                credentials=_credentials(settings),
                settings=settings,
                repository=mongo_repository,
            )
        )

    assert exc.value.status_code == 403
    assert "consent" in exc.value.detail.lower()


def test_current_user_allows_consented_user_and_consent_route(mongo_repository):
    settings = _settings()
    _create_user(mongo_repository, consented=True)
    user = asyncio.run(
        get_current_user(
            request=SimpleNamespace(url=SimpleNamespace(path="/me/summary")),
            credentials=_credentials(settings),
            settings=settings,
            repository=mongo_repository,
        )
    )
    assert user == AuthUser(
        user_id="user-1",
        email="patient@example.test",
        role="patient",
        has_consent=True,
    )

    unconsented_id = "consent-pending"
    _create_user(mongo_repository, user_id=unconsented_id)
    consent_route_user = asyncio.run(
        get_current_user(
            request=SimpleNamespace(url=SimpleNamespace(path="/me/consent")),
            credentials=_credentials(settings, user_id=unconsented_id),
            settings=settings,
            repository=mongo_repository,
        )
    )
    assert consent_route_user.user_id == unconsented_id


def test_test_tokens_are_development_only(mongo_repository):
    _create_user(mongo_repository)
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
                repository=mongo_repository,
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
            repository=mongo_repository,
        )
    )
    assert user.user_id == "user-1"


def test_rate_limit_rejects_requests_over_limit(mongo_repository):
    limiter = rate_limit("login", max_requests=1, window_seconds=900)
    request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    asyncio.run(limiter(request=request, repository=mongo_repository))
    with pytest.raises(HTTPException) as exc:
        asyncio.run(limiter(request=request, repository=mongo_repository))

    assert exc.value.status_code == 429
    assert "Retry-After" in exc.value.headers


def test_register_login_and_single_use_refresh_token(
    mongo_repository,
    monkeypatch,
):
    settings = _settings()
    monkeypatch.setattr(auth_routes, "get_settings", lambda: settings)

    registered = asyncio.run(
        auth_routes.register(
            auth_routes.RegisterBody(
                email="Patient@Example.Test",
                password="a-strong-password",
                full_name="Patient One",
            ),
            repository=mongo_repository,
        )
    )
    assert registered["role"] == "patient"
    assert registered["refresh_token"]
    assert registered["has_consent"] is False
    assert registered["onboarding_complete"] is False

    user = asyncio.run(mongo_repository.get_user(registered["user_id"]))
    assert user["email"] == "patient@example.test"
    assert user["password_hash"] != "a-strong-password"

    logged_in = asyncio.run(
        auth_routes.login(
            auth_routes.LoginBody(
                email="patient@example.test",
                password="a-strong-password",
            ),
            repository=mongo_repository,
        )
    )
    rotated = asyncio.run(
        auth_routes.refresh(
            auth_routes.RefreshBody(refresh_token=logged_in["refresh_token"]),
            repository=mongo_repository,
        )
    )
    assert rotated["refresh_token"] != logged_in["refresh_token"]

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            auth_routes.refresh(
                auth_routes.RefreshBody(refresh_token=logged_in["refresh_token"]),
                repository=mongo_repository,
            )
        )
    assert exc.value.status_code == 401


def test_login_migrates_bcrypt_password_hash_to_argon2(
    mongo_repository,
    monkeypatch,
):
    settings = _settings()
    monkeypatch.setattr(auth_routes, "get_settings", lambda: settings)
    password = b"existing-supabase-password"
    bcrypt_hash = bcrypt.hashpw(password, bcrypt.gensalt()).decode("ascii")
    asyncio.run(
        mongo_repository.create_user(
            {
                "_id": "migrated-user",
                "email": "migrated@example.test",
                "password_hash": bcrypt_hash,
                "role": "patient",
                "consent_version": "v1",
                "consent_at": datetime.now(timezone.utc),
            }
        )
    )

    session = asyncio.run(
        auth_routes.login(
            auth_routes.LoginBody(
                email="migrated@example.test",
                password=password.decode("ascii"),
            ),
            repository=mongo_repository,
        )
    )

    migrated_user = asyncio.run(mongo_repository.get_user("migrated-user"))
    assert session["access_token"]
    assert migrated_user["password_hash"].startswith("$argon2")
