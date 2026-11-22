from __future__ import annotations

from typing import Any

from bson import ObjectId
from bson.errors import InvalidId
from fastapi import Depends, HTTPException
from gridfs.errors import NoFile
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from app.mongo import get_mongo_database

MAX_LAB_REPORT_BYTES = 20 * 1024 * 1024


class LabReportStorage:
    def __init__(self, database: Any):
        self._bucket = AsyncIOMotorGridFSBucket(database, bucket_name="lab_reports")

    async def upload(
        self,
        patient_id: str,
        filename: str,
        content_type: str,
        content: bytes,
    ) -> str:
        if len(content) > MAX_LAB_REPORT_BYTES:
            raise HTTPException(status_code=413, detail="Lab report file is too large")
        upload_stream = self._bucket.open_upload_stream(
            filename,
            metadata={
                "patient_id": patient_id,
                "content_type": content_type,
                "original_filename": filename,
            },
        )
        await upload_stream.write(content)
        await upload_stream.close()
        return str(upload_stream._id)

    async def download(self, file_id: str, patient_id: str) -> tuple[bytes, dict[str, Any]]:
        try:
            object_id = ObjectId(file_id)
            download_stream = await self._bucket.open_download_stream(object_id)
        except (InvalidId, NoFile) as exc:
            raise HTTPException(status_code=404, detail="Lab report file not found") from exc

        metadata = download_stream.metadata or {}
        if metadata.get("patient_id") != patient_id:
            raise HTTPException(status_code=404, detail="Lab report file not found")
        return await download_stream.read(), metadata

    async def delete(self, file_id: str) -> None:
        await self._bucket.delete(ObjectId(file_id))

    async def delete_patient_files(self, patient_id: str) -> None:
        cursor = self._bucket.find({"metadata.patient_id": patient_id})
        async for stored_file in cursor:
            await self._bucket.delete(stored_file._id)


def get_lab_report_storage(database=Depends(get_mongo_database)) -> LabReportStorage:
    return LabReportStorage(database)
