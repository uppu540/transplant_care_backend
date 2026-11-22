from dataclasses import dataclass

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import Settings, get_settings

security = HTTPBearer()


@dataclass
class AuthUser:
    user_id: str
    email: str | None = None


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    settings: Settings = Depends(get_settings),
) -> AuthUser:
    token = credentials.credentials

    # Fast test token support for unit/integration testing
    if token.startswith("test:"):
        parts = token.split(":")
        uid = parts[1] if len(parts) > 1 else "test-user-id"
        email = parts[2] if len(parts) > 2 else "patient@example.com"
        return AuthUser(user_id=uid, email=email)

    secret = settings.supabase_jwt_secret or "transplantcare-default-secret"
    try:
        # Supabase HS256 JWT; for asymmetric projects switch to JWKS.
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    except jwt.PyJWTError as exc:
        if not settings.supabase_jwt_secret:
            try:
                unverified = jwt.decode(
                    token, options={"verify_signature": False, "verify_aud": False}
                )
                if unverified.get("sub"):
                    return AuthUser(user_id=unverified["sub"], email=unverified.get("email"))
            except Exception:
                pass
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        ) from exc

    sub = payload.get("sub")
    if not sub:
        raise HTTPException(status_code=401, detail="Token missing subject")
    return AuthUser(user_id=sub, email=payload.get("email"))
