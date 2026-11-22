from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, ClassVar
from uuid import uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, Field


def _utc_datetime(value: Any) -> Any:
    if not isinstance(value, datetime):
        return value
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


UTCDateTime = Annotated[datetime, AfterValidator(_utc_datetime)]


class MongoDocument(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid4()), alias="_id")

    model_config = ConfigDict(
        populate_by_name=True,
        extra="allow",
    )


class UserDocument(MongoDocument):
    collection_name: ClassVar[str] = "users"
    email: str
    password_hash: str
    role: str = "patient"
    created_at: UTCDateTime = Field(default_factory=lambda: datetime.now(timezone.utc))
    consent_version: str | None = None
    consent_at: UTCDateTime | None = None


class ProfileDocument(MongoDocument):
    collection_name: ClassVar[str] = "profiles"
    user_id: str
    full_name: str | None = None
    role: str = "patient"
    transplant_date: str | None = None
    doctor_name: str | None = None
    doctor_whatsapp: str | None = None
    locale: str | None = None


class MedicineDocument(MongoDocument):
    collection_name: ClassVar[str] = "medicines"
    patient_id: str
    name: str
    dose: str
    schedule_times: list[str] = Field(default_factory=list)
    active: bool = True
    notes: str | None = None


class DoseLogDocument(MongoDocument):
    collection_name: ClassVar[str] = "dose_logs"
    patient_id: str
    medicine_id: str
    scheduled_at: UTCDateTime
    status: str


class DietaryLimitDocument(MongoDocument):
    collection_name: ClassVar[str] = "dietary_limits"
    patient_id: str
    potassium_mg: float | None = None
    sodium_mg: float | None = None
    phosphorus_mg: float | None = None
    protein_g: float | None = None
    fluid_ml: float | None = None
    calories_kcal: float | None = None


class FoodLogDocument(MongoDocument):
    collection_name: ClassVar[str] = "food_logs"
    patient_id: str
    description: str
    meal_type: str = "other"
    logged_at: UTCDateTime | None = None
    potassium_mg: float | None = None
    sodium_mg: float | None = None
    phosphorus_mg: float | None = None
    protein_g: float | None = None
    fluid_ml: float | None = None
    calories_kcal: float | None = None
    estimate_source: str | None = None


class FoodCompositionDocument(MongoDocument):
    collection_name: ClassVar[str] = "food_composition"
    name_en: str
    potassium_mg: float | None = None
    sodium_mg: float | None = None
    phosphorus_mg: float | None = None
    protein_g: float | None = None
    fluid_ml: float | None = None
    calories_kcal: float | None = None


class LabReportDocument(MongoDocument):
    collection_name: ClassVar[str] = "lab_reports"
    patient_id: str
    collected_at: UTCDateTime
    storage_path: str
    extracted_at: UTCDateTime
    extraction_raw: dict[str, Any] = Field(default_factory=dict)


class LabValueDocument(MongoDocument):
    collection_name: ClassVar[str] = "lab_values"
    patient_id: str
    report_id: str
    analyte: str
    value: float
    unit: str | None = None
    ref_low: float | None = None
    ref_high: float | None = None
    out_of_range: bool = False
    measured_at: UTCDateTime


class EmergencyContactDocument(MongoDocument):
    collection_name: ClassVar[str] = "emergency_contacts"
    patient_id: str
    full_name: str
    phone: str
    relation: str | None = None
    sort_order: int = 0


class SosEventDocument(MongoDocument):
    collection_name: ClassVar[str] = "sos_events"
    patient_id: str
    lat: float | None = None
    lng: float | None = None
    symptoms: str | None = None
    status: str
    created_at: UTCDateTime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ConsentDocument(MongoDocument):
    collection_name: ClassVar[str] = "consents"
    user_id: str
    policy_version: str
    accepted: bool
    accepted_at: UTCDateTime = Field(default_factory=lambda: datetime.now(timezone.utc))


class CaregiverLinkDocument(MongoDocument):
    collection_name: ClassVar[str] = "caregiver_links"
    patient_id: str
    caregiver_user_id: str | None = None
    invite_code: str
    status: str = "pending"
    created_at: UTCDateTime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: UTCDateTime | None = None


class ChatMessageDocument(MongoDocument):
    collection_name: ClassVar[str] = "chat_messages"
    patient_id: str
    role: str
    content: str
    red_flag: bool = False
    tools_used: list[str] = Field(default_factory=list)


class AppointmentDocument(MongoDocument):
    collection_name: ClassVar[str] = "appointments"
    patient_id: str
    title: str | None = None
    scheduled_at: UTCDateTime | None = None
