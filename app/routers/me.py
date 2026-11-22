from datetime import date, datetime, timedelta, timezone
import secrets
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

from app.ai.tools import get_daily_budget
from app.auth import AuthUser, get_caregiver_user, get_current_user, get_patient_user
from app.repository import MongoRepository, get_repository
from app.storage import LabReportStorage, get_lab_report_storage
from app.rate_limit import rate_limit

router = APIRouter(prefix="/me", tags=["me"])


class ConsentBody(BaseModel):
    version: str
    accepted: bool = True


class ProfileUpdateBody(BaseModel):
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    transplant_date: date | None = None
    doctor_name: str | None = Field(default=None, max_length=200)
    doctor_whatsapp: str | None = Field(default=None, max_length=32)
    locale: str | None = Field(default=None, pattern="^(en|hi|te)$")


class EmergencyContactInput(BaseModel):
    full_name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=3, max_length=32)
    relation: str | None = Field(default=None, max_length=100)


class EmergencyContactsBody(BaseModel):
    contacts: list[EmergencyContactInput] = Field(max_length=5)


class DietaryLimitsBody(BaseModel):
    potassium_mg: float | None = Field(default=None, ge=0)
    sodium_mg: float | None = Field(default=None, ge=0)
    phosphorus_mg: float | None = Field(default=None, ge=0)
    protein_g: float | None = Field(default=None, ge=0)
    fluid_ml: float | None = Field(default=None, ge=0)
    calories_kcal: float | None = Field(default=None, ge=0)


@router.get("/status")
async def account_status(
    user: AuthUser = Depends(get_current_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    return {
        "role": user.role,
        "has_consent": user.has_consent,
        "onboarding_complete": (
            profile.get("onboarding_complete", True) if profile else False
        ),
    }


@router.get("/profile")
async def get_profile(
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    return {"profile": profile}


@router.patch("/profile")
async def update_profile(
    body: ProfileUpdateBody,
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    updates = body.model_dump(exclude_unset=True)
    if not updates:
        raise HTTPException(422, "At least one profile field is required")
    if "full_name" in updates and updates["full_name"] is not None:
        updates["full_name"] = updates["full_name"].strip()
        if not updates["full_name"]:
            raise HTTPException(422, "Full name cannot be blank")
    if isinstance(updates.get("transplant_date"), date):
        updates["transplant_date"] = updates["transplant_date"].isoformat()

    result = await repository.update_profile_by_user_id(user.user_id, updates)
    if result.matched_count != 1:
        raise HTTPException(404, "Profile not found")
    return await get_profile(user=user, repository=repository)


@router.post("/onboarding/complete")
async def complete_onboarding(
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    result = await repository.update_profile_by_user_id(
        user.user_id,
        {"onboarding_complete": True},
    )
    if result.matched_count != 1:
        raise HTTPException(404, "Profile not found")
    return {"ok": True}


@router.get("/emergency-contacts")
async def list_emergency_contacts(
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    contacts = await repository.find_patient_records(
        "emergency_contacts",
        profile["id"],
        sort=[("sort_order", 1)],
    )
    return {"contacts": contacts}


@router.put("/emergency-contacts")
async def replace_emergency_contacts(
    body: EmergencyContactsBody,
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    contacts = [
        {
            **contact.model_dump(),
            "sort_order": index,
        }
        for index, contact in enumerate(body.contacts)
    ]
    saved = await repository.replace_patient_records(
        "emergency_contacts",
        profile["id"],
        contacts,
    )
    return {"contacts": saved}


@router.get("/dietary-limits")
async def get_dietary_limits(
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    limits = await repository.find_one_patient_record(
        "dietary_limits",
        profile["id"],
    )
    return {"limits": limits}


@router.put("/dietary-limits")
async def set_dietary_limits(
    body: DietaryLimitsBody,
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    limits = await repository.upsert_patient_record(
        "dietary_limits",
        profile["id"],
        {},
        {**body.model_dump(), "set_by": "doctor"},
    )
    return {"limits": limits}


@router.get("/budget/today")
async def budget_today(
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    return await get_daily_budget(profile["id"], repository)


@router.get("/summary")
async def clinical_summary(
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    patient_id = profile["id"]
    meds = await repository.find_patient_records(
        "medicines",
        patient_id,
        filters={"active": True},
        projection={"name": 1, "dose": 1, "schedule_times": 1},
    )
    labs = await repository.find_patient_records(
        "lab_values",
        patient_id,
        projection={
            "analyte": 1,
            "value": 1,
            "unit": 1,
            "measured_at": 1,
            "out_of_range": 1,
        },
        sort=[("measured_at", -1)],
        limit=10,
    )
    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    today_logs = await repository.find_patient_records(
        "dose_logs",
        patient_id,
        filters={"scheduled_at": {"$gte": day_start, "$lt": day_end}},
        sort=[("scheduled_at", 1)],
    )
    today_food_logs = await repository.find_patient_records(
        "food_logs",
        patient_id,
        filters={"logged_at": {"$gte": day_start, "$lt": day_end}},
        sort=[("logged_at", -1)],
    )
    emergency_contacts = await repository.find_patient_records(
        "emergency_contacts",
        patient_id,
        sort=[("sort_order", 1)],
    )
    budget = await get_daily_budget(patient_id, repository)
    return {
        "profile": profile,
        "medicines": meds,
        "labs": labs,
        "today_logs": today_logs,
        "today_food_logs": today_food_logs,
        "emergency_contacts": emergency_contacts,
        "budget": budget,
    }


@router.post("/consent")
async def record_consent(
    body: ConsentBody,
    user: AuthUser = Depends(get_current_user),
    repository: MongoRepository = Depends(get_repository),
):
    now = datetime.now(timezone.utc)
    result = await repository.update_user(
        user.user_id,
        {
            "consent_version": body.version if body.accepted else None,
            "consent_at": now if body.accepted else None,
        },
    )
    if result.matched_count != 1:
        raise HTTPException(404, "User account not found")
    await repository.insert_consent(
        {
            "_id": str(uuid4()),
            "user_id": user.user_id,
            "policy_version": body.version,
            "accepted": body.accepted,
            "accepted_at": now,
        }
    )
    return {"ok": True}


@router.get("/export")
async def export_data(
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    patient_id = profile["id"]
    table_names = (
        "emergency_contacts",
        "medicines",
        "dose_logs",
        "dietary_limits",
        "food_logs",
        "appointments",
        "lab_reports",
        "lab_values",
        "chat_messages",
        "sos_events",
    )
    dump = {"profile": profile}
    for collection_name in table_names:
        dump[collection_name] = await repository.find_patient_records(
            collection_name,
            patient_id,
        )
    return dump


class AcceptInviteBody(BaseModel):
    invite_code: str


@router.post(
    "/caregiver/invite",
    dependencies=[Depends(rate_limit("caregiver_invite_create", max_requests=5))],
)
async def create_caregiver_invite(
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    patient_id = profile["id"]
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    link = None
    for _ in range(5):
        code = secrets.token_hex(4).upper()
        try:
            link = await repository.create_caregiver_invite(
                patient_id,
                code,
                expires_at,
            )
            break
        except DuplicateKeyError:
            continue
    if not link:
        raise HTTPException(503, "Unable to create a unique caregiver invite")
    return {
        "ok": True,
        "invite_code": link["invite_code"],
        "link": f"transplantcare://invite/{link['invite_code']}",
        "note": "Share this code with your caregiver to give them read-only view of your medications and budget.",
    }


@router.post(
    "/caregiver/accept",
    dependencies=[Depends(rate_limit("caregiver_invite_accept", max_requests=5))],
)
async def accept_caregiver_invite(
    body: AcceptInviteBody,
    user: AuthUser = Depends(get_current_user),
    repository: MongoRepository = Depends(get_repository),
):
    link = await repository.accept_caregiver_invite(
        body.invite_code.strip().upper(),
        user.user_id,
        datetime.now(timezone.utc),
    )
    if not link:
        raise HTTPException(404, "Invalid or expired invite code")
    result = await repository.update_user(user.user_id, {"role": "caregiver"})
    if result.matched_count != 1:
        raise HTTPException(404, "Caregiver account not found")
    await repository.update_profile_role(user.user_id, "caregiver")
    return {"ok": True, "patient_id": link["patient_id"]}


@router.get("/caregiver/patients")
async def list_caregiver_patients(
    user: AuthUser = Depends(get_caregiver_user),
    repository: MongoRepository = Depends(get_repository),
):
    links = await repository.list_active_caregiver_links(user.user_id)
    patients = []
    for link in links:
        profile = await repository.get_profile_by_id(link["patient_id"])
        if profile:
            patients.append(
                {
                    "id": profile["id"],
                    "full_name": profile.get("full_name"),
                    "transplant_date": profile.get("transplant_date"),
                }
            )
    return {"patients": patients}


@router.get("/caregiver/patient/{patient_id}")
async def get_caregiver_patient_view(
    patient_id: str,
    user: AuthUser = Depends(get_caregiver_user),
    repository: MongoRepository = Depends(get_repository),
):
    link = await repository.get_active_caregiver_link(patient_id, user.user_id)
    if not link:
        raise HTTPException(403, "Not authorized to view this patient")

    profile = await repository.get_profile_by_id(patient_id)
    if not profile:
        raise HTTPException(404, "Patient profile not found")
    patient = {
        "id": profile["id"],
        "full_name": profile.get("full_name"),
        "transplant_date": profile.get("transplant_date"),
        "doctor_name": profile.get("doctor_name"),
        "doctor_whatsapp": profile.get("doctor_whatsapp"),
    }
    meds = await repository.find_patient_records(
        "medicines",
        patient_id,
        filters={"active": True},
        projection={"name": 1, "dose": 1, "schedule_times": 1},
    )
    today_start = datetime.now(timezone.utc).replace(
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )
    logs = await repository.find_patient_records(
        "dose_logs",
        patient_id,
        filters={"scheduled_at": {"$gte": today_start}},
        projection={"medicine_id": 1, "scheduled_at": 1, "status": 1},
    )
    budget = await get_daily_budget(patient_id, repository)
    labs = await repository.find_patient_records(
        "lab_values",
        patient_id,
        projection={
            "analyte": 1,
            "value": 1,
            "unit": 1,
            "ref_low": 1,
            "ref_high": 1,
            "out_of_range": 1,
            "measured_at": 1,
        },
        sort=[("measured_at", -1)],
        limit=10,
    )
    contacts = await repository.find_patient_records(
        "emergency_contacts",
        patient_id,
        projection={"full_name": 1, "phone": 1, "relation": 1, "sort_order": 1},
        sort=[("sort_order", 1)],
    )
    return {
        "read_only": True,
        "patient": patient,
        "medicines": meds,
        "today_logs": logs,
        "budget": budget,
        "recent_labs": labs,
        "emergency_contacts": contacts,
        "disclaimer": "Read-only caregiver view. Does not replace medical care.",
    }


@router.delete("")
async def delete_account(
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
    storage: LabReportStorage = Depends(get_lab_report_storage),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    await storage.delete_patient_files(profile["id"])
    await repository.delete_account(profile["id"], user.user_id)
    return {"ok": True, "note": "Profile and related data deleted."}
