from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.config import Settings, get_settings
from app.mongo import get_mongo_database

security = HTTPBearer()


@dataclass
class AuthUser:
    user_id: str
    email: str | None = None
    role: str = "patient"


@lru_cache
def get_jwks_client(supabase_url: str) -> PyJWKClient:
    return PyJWKClient(
        f"{supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
    )


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


def _verify_supabase_token(token: str, settings: Settings) -> dict[str, Any]:
    if not settings.supabase_url:
        raise HTTPException(
            status_code=401,
            detail="Invalid or expired token",
        )

    header = jwt.get_unverified_header(token)
    algorithm = header.get("alg")

    if algorithm == "HS256":
        if not settings.supabase_jwt_secret:
            raise HTTPException(
                status_code=401,
                detail="Legacy Supabase token verification is not configured",
            )
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
            issuer=f"{settings.supabase_url.rstrip('/')}/auth/v1",
        )

    if algorithm in ("ES256", "RS256"):
        signing_key = get_jwks_client(
            settings.supabase_url
        ).get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=[algorithm],
            audience="authenticated",
            issuer=f"{settings.supabase_url.rstrip('/')}/auth/v1",
        )

    raise HTTPException(status_code=401, detail="Unsupported token algorithm")


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials = Depends(security),
    settings: Settings = Depends(get_settings),
    database=Depends(get_mongo_database),
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
            unverified = jwt.decode(token, options={"verify_signature": False})
            if unverified.get("iss") == settings.jwt_issuer:
                payload = _verify_local_token(token, settings)
            else:
                payload = _verify_supabase_token(token, settings)
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

    if request.url.path not in ("/me/consent", "/auth/logout"):
        user_record = await database["users"].find_one(
            {"_id": user_id},
            {"consent_version": 1, "consent_at": 1, "role": 1},
        )
        if (
            not user_record
            or not user_record.get("consent_version")
            or not user_record.get("consent_at")
        ):
            raise HTTPException(
                status_code=403,
                detail="Medical consent is required before accessing data",
            )

    return AuthUser(
        user_id=user_id,
        email=payload.get("email"),
        role=payload.get("role", "patient"),
    )
