from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.auth import AuthUser, create_access_token, get_current_user
from app.config import get_settings
from app.mongo import get_mongo_database
from app.rate_limit import rate_limit

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


def _require_jwt_configuration() -> None:
    if len(get_settings().jwt_secret) < 32:
        raise HTTPException(
            status_code=500,
            detail="JWT_SECRET must contain at least 32 characters",
        )


async def _issue_session(user: dict, database) -> dict:
    settings = get_settings()
    access_token = create_access_token(
        user_id=user["_id"],
        email=user["email"],
        role=user["role"],
        settings=settings,
    )
    refresh_token = secrets.token_urlsafe(48)
    now = datetime.now(timezone.utc)
    await database["refresh_tokens"].insert_one(
        {
            "_id": str(uuid4()),
            "user_id": user["_id"],
            "token_hash": _hash_refresh_token(refresh_token),
            "issued_at": now,
            "expires_at": now + timedelta(days=settings.refresh_token_days),
            "revoked_at": None,
        }
    )
    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": settings.access_token_minutes * 60,
    }


@router.post("/register", status_code=status.HTTP_201_CREATED)
async def register(body: RegisterBody, database=Depends(get_mongo_database)):
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
        await database["users"].insert_one(user)
    except DuplicateKeyError as exc:
        raise HTTPException(status_code=409, detail="Email is already registered") from exc

    await database["profiles"].insert_one(
        {
            "_id": str(uuid4()),
            "user_id": user_id,
            "full_name": full_name,
            "role": body.role,
        }
    )
    session = await _issue_session(user, database)
    return {"user_id": user_id, "role": body.role, **session}


@router.post(
    "/login",
    dependencies=[Depends(rate_limit("login", max_requests=5))],
)
async def login(
    body: LoginBody,
    database=Depends(get_mongo_database),
):
    _require_jwt_configuration()
    email = _normalize_email(body.email)
    user = await database["users"].find_one({"email": email})
    if not user:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    try:
        password_hasher.verify(user["password_hash"], body.password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if password_hasher.check_needs_rehash(user["password_hash"]):
        user["password_hash"] = password_hasher.hash(body.password)
        await database["users"].update_one(
            {"_id": user["_id"]},
            {"$set": {"password_hash": user["password_hash"]}},
        )
    session = await _issue_session(user, database)
    return {"user_id": user["_id"], "role": user.get("role", "patient"), **session}


@router.post("/refresh")
async def refresh(body: RefreshBody, database=Depends(get_mongo_database)):
    _require_jwt_configuration()
    now = datetime.now(timezone.utc)
    token_hash = _hash_refresh_token(body.refresh_token)
    record = await database["refresh_tokens"].find_one_and_update(
        {
            "token_hash": token_hash,
            "revoked_at": None,
            "expires_at": {"$gt": now},
        },
        {"$set": {"revoked_at": now}},
        return_document=ReturnDocument.BEFORE,
    )
    if not record:
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = await database["users"].find_one({"_id": record["user_id"]})
    if not user:
        raise HTTPException(status_code=401, detail="Account no longer exists")
    session = await _issue_session(user, database)
    return {"user_id": user["_id"], "role": user.get("role", "patient"), **session}


@router.post("/logout")
async def logout(
    body: RefreshBody,
    user: AuthUser = Depends(get_current_user),
    database=Depends(get_mongo_database),
):
    result = await database["refresh_tokens"].update_one(
        {
            "user_id": user.user_id,
            "token_hash": _hash_refresh_token(body.refresh_token),
            "revoked_at": None,
        },
        {"$set": {"revoked_at": datetime.now(timezone.utc)}},
    )
    if result.modified_count != 1:
        raise HTTPException(status_code=401, detail="Invalid or already revoked refresh token")
    return {"ok": True}
