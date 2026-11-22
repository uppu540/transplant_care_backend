from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import uuid4

from fastapi import Depends
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.mongo import get_mongo_database
from app.models import (
    AppointmentDocument,
    CaregiverLinkDocument,
    ChatMessageDocument,
    ConsentDocument,
    DietaryLimitDocument,
    DoseLogDocument,
    EmergencyContactDocument,
    FoodLogDocument,
    LabReportDocument,
    LabValueDocument,
    MedicineDocument,
    ProfileDocument,
    RefreshTokenDocument,
    SosEventDocument,
    UserDocument,
)


PATIENT_COLLECTIONS = frozenset(
    {
        "appointments",
        "caregiver_links",
        "chat_messages",
        "dietary_limits",
        "dose_logs",
        "emergency_contacts",
        "food_logs",
        "lab_reports",
        "lab_values",
        "medicines",
        "sos_events",
    }
)

PATIENT_DOCUMENT_MODELS = {
    "appointments": AppointmentDocument,
    "caregiver_links": CaregiverLinkDocument,
    "chat_messages": ChatMessageDocument,
    "dietary_limits": DietaryLimitDocument,
    "dose_logs": DoseLogDocument,
    "emergency_contacts": EmergencyContactDocument,
    "food_logs": FoodLogDocument,
    "lab_reports": LabReportDocument,
    "lab_values": LabValueDocument,
    "medicines": MedicineDocument,
    "sos_events": SosEventDocument,
}


def _serialize(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    result = dict(document)
    document_id = result.pop("_id", None)
    if document_id is not None:
        result.setdefault("id", str(document_id))
    return result


def _validate_document(model, document: dict[str, Any]) -> dict[str, Any]:
    return model.model_validate(document).model_dump(
        mode="python",
        by_alias=True,
        exclude_none=True,
    )


class MongoRepository:
    def __init__(self, database: Any):
        self._database = database

    async def get_user(self, user_id: str) -> dict[str, Any] | None:
        return await self._database["users"].find_one({"_id": user_id})

    async def get_user_by_email(self, email: str) -> dict[str, Any] | None:
        return await self._database["users"].find_one({"email": email})

    async def create_user(self, user: dict[str, Any]) -> None:
        await self._database["users"].insert_one(
            _validate_document(UserDocument, user)
        )

    async def update_user(self, user_id: str, updates: dict[str, Any]) -> Any:
        return await self._database["users"].update_one(
            {"_id": user_id},
            {"$set": updates},
        )

    async def create_profile(self, profile: dict[str, Any]) -> None:
        await self._database["profiles"].insert_one(
            _validate_document(ProfileDocument, profile)
        )

    async def get_profile_by_user_id(self, user_id: str) -> dict[str, Any] | None:
        return _serialize(
            await self._database["profiles"].find_one({"user_id": user_id})
        )

    async def get_profile_by_id(self, patient_id: str) -> dict[str, Any] | None:
        return _serialize(
            await self._database["profiles"].find_one({"_id": patient_id})
        )

    async def update_profile_by_user_id(
        self,
        user_id: str,
        updates: dict[str, Any],
    ) -> Any:
        return await self._database["profiles"].update_one(
            {"user_id": user_id},
            {"$set": updates},
        )

    async def update_profile_role(self, user_id: str, role: str) -> Any:
        return await self._database["profiles"].update_one(
            {"user_id": user_id},
            {"$set": {"role": role}},
        )

    async def get_daily_budget(self, patient_id: str) -> dict[str, Any]:
        limits = await self.find_one_patient_record(
            "dietary_limits",
            patient_id,
        )
        now = datetime.now(timezone.utc)
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end = start + timedelta(days=1)
        nutrients = (
            "potassium_mg",
            "sodium_mg",
            "phosphorus_mg",
            "protein_g",
            "fluid_ml",
            "calories_kcal",
        )
        foods = await self.find_patient_records(
            "food_logs",
            patient_id,
            filters={"logged_at": {"$gte": start, "$lt": end}},
            projection={key: 1 for key in nutrients},
        )
        consumed = {
            key: float(sum((row.get(key) or 0) for row in foods))
            for key in nutrients
        }
        remaining = None
        if limits:
            remaining = {
                key: (
                    None
                    if limits.get(key) is None
                    else float(limits[key]) - consumed[key]
                )
                for key in nutrients
            }
        return {
            "limits_set": limits is not None,
            "limits": limits,
            "consumed": consumed,
            "remaining": remaining,
        }

    async def list_food_composition(
        self,
        projection: dict[str, int] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        cursor = self._database["food_composition"].find({}, projection)
        if limit is not None:
            cursor = cursor.limit(limit)
        return await cursor.to_list(length=limit)

    async def delete_account(self, patient_id: str, user_id: str) -> None:
        for collection_name in PATIENT_COLLECTIONS:
            if collection_name == "caregiver_links":
                continue
            await self._database[collection_name].delete_many(
                {"patient_id": patient_id}
            )
        await self._database["caregiver_links"].delete_many(
            {
                "$or": [
                    {"patient_id": patient_id},
                    {"caregiver_user_id": user_id},
                ]
            }
        )
        await self._database["profiles"].delete_one(
            {"_id": patient_id, "user_id": user_id}
        )
        await self._database["consents"].delete_many({"user_id": user_id})
        await self._database["refresh_tokens"].delete_many({"user_id": user_id})
        await self._database["users"].delete_one({"_id": user_id})

    async def find_patient_records(
        self,
        collection_name: str,
        patient_id: str,
        filters: dict[str, Any] | None = None,
        projection: dict[str, int] | None = None,
        sort: list[tuple[str, int]] | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self._validate_patient_collection(collection_name)
        query = dict(filters or {})
        query.pop("patient_id", None)
        query["patient_id"] = patient_id
        cursor = self._database[collection_name].find(query, projection)
        if sort:
            cursor = cursor.sort(sort)
        if limit is not None:
            cursor = cursor.limit(limit)
        records = await cursor.to_list(length=limit)
        return [_serialize(record) or {} for record in records]

    async def find_one_patient_record(
        self,
        collection_name: str,
        patient_id: str,
        filters: dict[str, Any] | None = None,
        projection: dict[str, int] | None = None,
        sort: list[tuple[str, int]] | None = None,
    ) -> dict[str, Any] | None:
        records = await self.find_patient_records(
            collection_name,
            patient_id,
            filters=filters,
            projection=projection,
            sort=sort,
            limit=1,
        )
        return records[0] if records else None

    async def insert_patient_record(
        self,
        collection_name: str,
        patient_id: str,
        document: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_patient_collection(collection_name)
        payload = dict(document)
        document_id = str(payload.pop("id", None) or uuid4())
        payload.pop("_id", None)
        payload["patient_id"] = patient_id
        payload["_id"] = document_id
        payload.setdefault("created_at", datetime.now(timezone.utc))
        payload = _validate_document(
            PATIENT_DOCUMENT_MODELS[collection_name],
            payload,
        )
        await self._database[collection_name].insert_one(payload)
        return _serialize(payload) or {}

    async def replace_patient_records(
        self,
        collection_name: str,
        patient_id: str,
        documents: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        self._validate_patient_collection(collection_name)
        model = PATIENT_DOCUMENT_MODELS[collection_name]
        prepared = []
        now = datetime.now(timezone.utc)
        for document in documents:
            payload = dict(document)
            payload.pop("_id", None)
            payload["patient_id"] = patient_id
            payload.setdefault("created_at", now)
            prepared.append(_validate_document(model, payload))

        await self._database[collection_name].delete_many(
            {"patient_id": patient_id}
        )
        if not prepared:
            return []
        result = await self._database[collection_name].insert_many(prepared)
        return [
            _serialize({**document, "_id": inserted_id}) or {}
            for document, inserted_id in zip(prepared, result.inserted_ids)
        ]

    async def upsert_patient_record(
        self,
        collection_name: str,
        patient_id: str,
        identity: dict[str, Any],
        document: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_patient_collection(collection_name)
        query = dict(identity)
        query.pop("patient_id", None)
        query["patient_id"] = patient_id
        payload = dict(document)
        payload.pop("_id", None)
        payload.pop("id", None)
        payload["patient_id"] = patient_id
        payload = _validate_document(
            PATIENT_DOCUMENT_MODELS[collection_name],
            {
                **payload,
                "_id": str(uuid4()),
                "created_at": datetime.now(timezone.utc),
            },
        )
        document_id = payload.pop("_id")
        created_at = payload.pop("created_at")
        for key in identity:
            if key in payload:
                query[key] = payload[key]
        result = await self._database[collection_name].find_one_and_update(
            query,
            {
                "$set": payload,
                "$setOnInsert": {
                    "_id": document_id,
                    "created_at": created_at,
                },
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return _serialize(result) or {}

    async def update_patient_record(
        self,
        collection_name: str,
        patient_id: str,
        record_id: str,
        updates: dict[str, Any],
    ) -> Any:
        self._validate_patient_collection(collection_name)
        payload = dict(updates)
        for reserved in ("_id", "id", "patient_id"):
            payload.pop(reserved, None)
        return await self._database[collection_name].update_one(
            {"_id": record_id, "patient_id": patient_id},
            {"$set": payload},
        )

    async def delete_patient_record(
        self,
        collection_name: str,
        patient_id: str,
        record_id: str,
    ) -> Any:
        self._validate_patient_collection(collection_name)
        return await self._database[collection_name].delete_one(
            {"_id": record_id, "patient_id": patient_id}
        )

    async def delete_patient_records(
        self,
        collection_name: str,
        patient_id: str,
        filters: dict[str, Any],
    ) -> Any:
        self._validate_patient_collection(collection_name)
        query = dict(filters)
        query.pop("patient_id", None)
        query["patient_id"] = patient_id
        return await self._database[collection_name].delete_many(query)

    async def insert_consent(self, consent: dict[str, Any]) -> None:
        await self._database["consents"].insert_one(
            _validate_document(ConsentDocument, consent)
        )

    async def add_refresh_token(self, token: dict[str, Any]) -> None:
        await self._database["refresh_tokens"].insert_one(
            _validate_document(RefreshTokenDocument, token)
        )

    async def rotate_refresh_token(
        self,
        token_hash: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        return await self._database["refresh_tokens"].find_one_and_update(
            {
                "token_hash": token_hash,
                "revoked_at": None,
                "expires_at": {"$gt": now},
            },
            {"$set": {"revoked_at": now}},
            return_document=ReturnDocument.BEFORE,
        )

    async def revoke_refresh_token(
        self,
        user_id: str,
        token_hash: str,
        now: datetime,
    ) -> Any:
        return await self._database["refresh_tokens"].update_one(
            {
                "user_id": user_id,
                "token_hash": token_hash,
                "revoked_at": None,
            },
            {"$set": {"revoked_at": now}},
        )

    async def increment_rate_limit(
        self,
        bucket_id: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        collection = self._database["rate_limits"]
        try:
            result = await collection.find_one_and_update(
                {"_id": bucket_id},
                {
                    "$inc": {"count": 1},
                    "$setOnInsert": {"expires_at": expires_at},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            result = await collection.find_one_and_update(
                {"_id": bucket_id},
                {"$inc": {"count": 1}},
                return_document=ReturnDocument.AFTER,
            )
        if result is None:
            raise RuntimeError("Failed to update rate limit counter")
        return result

    async def create_caregiver_invite(
        self,
        patient_id: str,
        invite_code: str,
        expires_at: datetime,
    ) -> dict[str, Any]:
        document = {
            "_id": str(uuid4()),
            "patient_id": patient_id,
            "invite_code": invite_code,
            "status": "pending",
            "created_at": datetime.now(timezone.utc),
            "expires_at": expires_at,
        }
        document = _validate_document(CaregiverLinkDocument, document)
        await self._database["caregiver_links"].insert_one(document)
        return _serialize(document) or {}

    async def accept_caregiver_invite(
        self,
        invite_code: str,
        caregiver_user_id: str,
        now: datetime,
    ) -> dict[str, Any] | None:
        return _serialize(
            await self._database["caregiver_links"].find_one_and_update(
                {
                    "invite_code": invite_code,
                    "status": "pending",
                    "expires_at": {"$gt": now},
                },
                {
                    "$set": {
                        "caregiver_user_id": caregiver_user_id,
                        "status": "active",
                    },
                    "$unset": {"expires_at": ""},
                },
                return_document=ReturnDocument.BEFORE,
            )
        )

    async def get_active_caregiver_link(
        self,
        patient_id: str,
        caregiver_user_id: str,
    ) -> dict[str, Any] | None:
        return _serialize(
            await self._database["caregiver_links"].find_one(
                {
                    "patient_id": patient_id,
                    "caregiver_user_id": caregiver_user_id,
                    "status": "active",
                }
            )
        )

    async def list_active_caregiver_links(
        self,
        caregiver_user_id: str,
    ) -> list[dict[str, Any]]:
        records = await self._database["caregiver_links"].find(
            {
                "caregiver_user_id": caregiver_user_id,
                "status": "active",
            }
        ).to_list(length=None)
        return [_serialize(record) or {} for record in records]

    @staticmethod
    def _validate_patient_collection(collection_name: str) -> None:
        if collection_name not in PATIENT_COLLECTIONS:
            raise ValueError(f"Unsupported patient collection: {collection_name}")


def get_repository(database=Depends(get_mongo_database)):
    return MongoRepository(database)
