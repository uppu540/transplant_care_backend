from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request

from app.repository import MongoRepository, get_repository


def rate_limit(scope: str, max_requests: int, window_seconds: int = 900):
    async def enforce_rate_limit(
        request: Request,
        repository: MongoRepository = Depends(get_repository),
    ) -> None:
        now = datetime.now(timezone.utc)
        window_start = int(now.timestamp()) // window_seconds
        client_host = request.client.host if request.client else "unknown"
        bucket_id = hashlib.sha256(
            f"{scope}:{client_host}:{window_start}".encode("utf-8")
        ).hexdigest()
        expires_at = datetime.fromtimestamp(
            (window_start + 1) * window_seconds,
            tz=timezone.utc,
        )
        bucket = await repository.increment_rate_limit(bucket_id, expires_at)
        if bucket["count"] > max_requests:
            retry_after = max(1, int((expires_at - now).total_seconds()))
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(retry_after)},
            )

    return enforce_rate_limit
