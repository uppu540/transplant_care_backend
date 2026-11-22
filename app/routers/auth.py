from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

import bcrypt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.auth import AuthUser, create_access_token, get_current_user
from app.config import get_settings
from app.rate_limit import rate_limit
from app.repository import MongoRepository, get_repository

router = APIRouter(prefix="/auth", tags=["auth"])
password_hasher = PasswordHasher()


class RegisterBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=12, max_length=256)
    full_name: str = Field(min_length=1, max_length=200)
    role: Literal["patient", "caregiver"] = "patient"


class LoginBody(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class RefreshBody(BaseModel):
    refresh_token: str = Field(min_length=32, max_length=256)


def _normalize_email(email: str) -> str:
    normalized = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", normalized):
        raise HTTPException(status_code=422, detail="Invalid email address")
    return normalized


def _hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _verify_password(encoded_hash: str, password: str) -> bool:
    try:
        if encoded_hash.startswith(("$2a$", "$2b$", "$2y$")):
            return bcrypt.checkpw(password.encode("utf-8"), encoded_hash.encode("ascii"))
        password_hasher.verify(encoded_hash, password)
        return True
    except (ValueError, VerificationError, InvalidHashError):
        return False


def _password_needs_rehash(encoded_hash: str) -> bool:
    return not encoded_hash.startswith("$argon2") or password_hasher.check_needs_rehash(
        encoded_hash
    )


def _require_jwt_configuration() -> None:
    if len(get_settings().jwt_secret) < 32:
        raise HTTPException(
            status_code=500,
            detail="JWT_SECRET must contain at least 32 characters",
        )


async def _issue_session(user: dict, repository: MongoRepository) -> dict:
    settings = get_settings()
    access_token = create_access_token(
        user_id=user["_id"],
        email=user["email"],
        role=user["role"],
        settings=settings,
    )
    refresh_token = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    await repository.add_refresh_token(
        {
            "_id": str(uuid4()),
            "user_id": user["_id"],
            "token_hash": _hash_refresh_token(refresh_token),
            "issued_at": now,
            "expires_at": now + timedelta(days=settings.refresh_token_days),
            "revoked_at": None,
        }
    )
    profile = await repository.get_profile_by_user_id(user["_id"])
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.access_token_minutes * 60,
        "has_consent": bool(user.get("consent_version") and user.get("consent_at")),
        "onboarding_complete": (
            profile.get("onboarding_complete", True) if profile else False
        ),
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(
    body: RegisterBody,
    repository: MongoRepository = Depends(get_repository),
):
    _require_jwt_configuration()
    email = _normalize_email(body.email)
    full_name = body.full_name.strip()
    if not full_name:
        raise HTTPException(status_code=422, detail="Full name cannot be blank")
    user_id = str(uuid4())
    user = {
        "_id": user_id,
        "email": email,
        "password_hash": password_hasher.hash(body.password),
        "role": body.role,
        "created_at": datetime.now(timezone.utc),
        "consent_version": None,
        "consent_at": None,
    }
    try:
        await repository.create_user(user)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Email is already registered") from exc

    await repository.create_profile(
        {
            "_id": str(uuid4()),
            "user_id": user_id,
            "full_name": full_name,
            "role": body.role,
            "onboarding_complete": False,
        }
    )
    session = await _issue_session(user, repository)
    return {"user_id": user_id, "role": body.role, **session}


@router.post(
    "/login",
    dependencies=[Depends(rate_limit("login", max_requests=5))],
)
async def login(
    body: LoginBody,
    repository: MongoRepository = Depends(get_repository),
):
    _require_jwt_configuration()
    email = _normalize_email(body.email)
    user = await repository.get_user_by_email(email)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not _verify_password(user["password_hash"], body.password):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if _password_needs_rehash(user["password_hash"]):
        user["password_hash"] = password_hasher.hash(body.password)
        await repository.update_user(
            user["_id"],
            {"password_hash": user["password_hash"]},
        )
    session = await _issue_session(user, repository)
    return {"user_id": user["_id"], "role": user.get("role", "patient"), **session}


@router.post("/refresh")
async def refresh(
    body: RefreshBody,
    repository: MongoRepository = Depends(get_repository),
):
    _require_jwt_configuration()
    now = datetime.now(timezone.utc)
    token_hash = _hash_refresh_token(body.refresh_token)
    record = await repository.rotate_refresh_token(token_hash, now)
    if not record:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = await repository.get_user(record["user_id"])
    if not user:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    session = await _issue_session(user, repository)
    return {"user_id": user["_id"], "role": user.get("role", "patient"), **session}


@router.post("/logout")
async def logout(
    body: RefreshBody,
    user: AuthUser = Depends(get_current_user),
    repository: MongoRepository = Depends(get_repository),
):
    result = await repository.revoke_refresh_token(
        user.user_id,
        _hash_refresh_token(body.refresh_token),
        datetime.now(timezone.utc),
    )
    if result.modified_count != 1:
        raise HTTPException(status_code=401, detail="Invalid or already revoked refresh token")
    return {"ok": True}
