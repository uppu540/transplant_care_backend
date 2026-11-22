from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings
from app.repository import MongoRepository, get_repository

security = HTTPBearer()


@dataclass
class AuthUser:
    user_id: str
    email: str | None = None
    role: str = "patient"
    has_consent: bool = False


def create_access_token(
    user_id: str,
    email: str,
    role: str,
    settings: Settings | None = None,
) -> str:
    settings = settings or get_settings()
    if len(settings.jwt_secret) < 32:
        raise RuntimeError("JWT_SECRET must contain at least 32 characters")
    now = datetime.now(timezone.utc)
    issued_at = int(now.timestamp())
    return jwt.encode(
        {
            "sub": user_id,
            "email": email,
            "role": role,
            "type": "access",
            "iat": issued_at,
            "exp": issued_at + settings.access_token_minutes * 60,
            "aud": "transplantcare",
            "iss": settings.jwt_issuer,
        },
        settings.jwt_secret,
        algorithm="HS256",
    )


def _verify_local_token(token: str, settings: Settings) -> dict[str, Any]:
    if len(settings.jwt_secret) < 32:
        raise HTTPException(
            status_code=500,
            detail="JWT_SECRET must contain at least 32 characters",
        )
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=["HS256"],
        audience="transplantcare",
        issuer=settings.jwt_issuer,
    )
    if payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Invalid access token")
    return payload


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    settings: Settings = Depends(get_settings),
    repository: MongoRepository = Depends(get_repository),
) -> AuthUser:
    token = credentials.credentials

    if token.startswith("test:"):
        if settings.app_env != "development":
            raise HTTPException(status_code=401, detail="Test tokens are disabled")
        parts = token.split(":")
        payload = {
            "sub": parts[1] if len(parts) > 1 else "test-user-id",
            "email": parts[2] if len(parts) > 2 else "patient@example.com",
            "role": "patient",
        }
    else:
        try:
            payload = _verify_local_token(token, settings)
        except HTTPException:
            raise
        except jwt.PyJWTError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired token",
            ) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Token missing subject")

    user_record = await repository.get_user(user_id)
    if not user_record:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    if request.url.path not in ("/me/consent", "/me/status", "/auth/logout") and (
        not user_record.get("consent_version") or not user_record.get("consent_at")
    ):
        raise HTTPException(
            status_code=403,
            detail="Medical consent is required before accessing data",
        )

    return AuthUser(
        user_id=user_id,
        email=payload.get("email"),
        role=user_record.get("role", payload.get("role", "patient")),
        has_consent=bool(user_record.get("consent_version") and user_record.get("consent_at")),
    )


async def get_patient_user(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if user.role != "patient":
        raise HTTPException(status_code=403, detail="Patient access required")
    return user


async def get_caregiver_user(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if user.role != "caregiver":
        raise HTTPException(status_code=403, detail="Caregiver access required")
    return user
