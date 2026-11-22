import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.auth import create_access_token
from app.config import Settings, get_settings
from app.main import app
from app.repository import get_repository


def _create_user(repository, user_id, role="patient", consented=False):
    asyncio.run(
        repository.create_user(
            {
                "_id": user_id,
                "email": f"{user_id}@example.test",
                "password_hash": "test-password-hash",
                "role": role,
                "consent_version": "v1" if consented else None,
                "consent_at": datetime.now(timezone.utc) if consented else None,
            }
        )
    )


def test_unauthenticated_unconsented_and_caregiver_write_access(
    mongo_repository,
):
    settings = Settings(
        _env_file=None,
        jwt_secret="access-control-test-secret-with-at-least-32-bytes",
        jwt_issuer="transplantcare",
    )
    _create_user(mongo_repository, "patient-a")
    asyncio.run(
        mongo_repository.create_profile(
            {
                "_id": "profile-a",
                "user_id": "patient-a",
                "full_name": "Patient A",
            }
        )
    )
    _create_user(mongo_repository, "caregiver-a", role="caregiver", consented=True)

    @asynccontextmanager
    async def no_database_lifespan(application):
        yield

    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = no_database_lifespan
    app.dependency_overrides[get_repository] = lambda: mongo_repository
    app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(app) as client:
            unauthenticated = client.get("/me/summary")
            assert unauthenticated.status_code in (401, 403)

            patient_token = create_access_token(
                "patient-a",
                "patient-a@example.test",
                "patient",
                settings,
            )
            headers = {"Authorization": f"Bearer {patient_token}"}
            before_consent = client.get("/me/summary", headers=headers)
            assert before_consent.status_code == 403

            consent = client.post(
                "/me/consent",
                headers=headers,
                json={"version": "medical-v1", "accepted": True},
            )
            assert consent.status_code == 200
            after_consent = client.get("/me/summary", headers=headers)
            assert after_consent.status_code == 200

            caregiver_token = create_access_token(
                "caregiver-a",
                "caregiver-a@example.test",
                "caregiver",
                settings,
            )
            caregiver_write = client.post(
                "/food/log",
                headers={"Authorization": f"Bearer {caregiver_token}"},
                json={"description": "meal"},
            )
            assert caregiver_write.status_code == 403
    finally:
        app.dependency_overrides.clear()
        app.router.lifespan_context = original_lifespan
