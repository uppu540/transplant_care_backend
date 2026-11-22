from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from fastapi import Depends, HTTPException, Request
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.mongo import get_mongo_database


def rate_limit(scope: str, max_requests: int, window_seconds: int = 900):
    async def enforce_rate_limit(
        request: Request,
        database=Depends(get_mongo_database),
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
        collection = database["rate_limits"]
        try:
            bucket = await collection.find_one_and_update(
                {"_id": bucket_id},
                {
                    "$inc": {"count": 1},
                    "$setOnInsert": {"expires_at": expires_at},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            bucket = await collection.find_one_and_update(
                {"_id": bucket_id},
                {"$inc": {"count": 1}},
                return_document=ReturnDocument.AFTER,
            )

        if bucket is None:
            raise RuntimeError("Failed to update rate limit counter")
        if bucket["count"] > max_requests:
            retry_after = max(1, int((expires_at - now).total_seconds()))
            raise HTTPException(
                status_code=429,
                detail="Too many requests",
                headers={"Retry-After": str(retry_after)},
            )

    return enforce_rate_limit
