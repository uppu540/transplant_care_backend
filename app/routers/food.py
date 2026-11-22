from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import AuthUser, get_patient_user
from app.repository import MongoRepository, get_repository
from app.services.food_estimate import estimate_from_text

router = APIRouter(prefix="/food", tags=["food"])


class EstimateBody(BaseModel):
    text: str = Field(min_length=1)


class LogBody(BaseModel):
    description: str
    meal_type: str = "other"
    potassium_mg: float | None = None
    sodium_mg: float | None = None
    phosphorus_mg: float | None = None
    protein_g: float | None = None
    fluid_ml: float | None = None
    calories_kcal: float | None = None
    estimate_source: str = "llm"


@router.post("/estimate")
async def estimate(
    body: EstimateBody,
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    return await estimate_from_text(body.text, repository)


@router.post("/log")
async def log_food(
    body: LogBody,
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    row = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    log = await repository.insert_patient_record("food_logs", profile["id"], row)
    return {"ok": True, "log": [log]}


@router.get("/suggestions")
async def suggestions(
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    from app.ai.tools import get_daily_budget

    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    budget = await get_daily_budget(profile["id"], repository)
    if not budget["limits_set"]:
        raise HTTPException(
            400,
            "No doctor-set dietary limits. Please enter limits from your doctor first.",
        )
    remaining = budget["remaining"] or {}
    # Simple rule-based ideas from composition DB for MVP
    foods = await repository.list_food_composition(limit=40)

    def fits(f: dict) -> bool:
        checks = [
            ("potassium_mg", "potassium_mg"),
            ("sodium_mg", "sodium_mg"),
            ("phosphorus_mg", "phosphorus_mg"),
            ("protein_g", "protein_g"),
            ("calories_kcal", "calories_kcal"),
        ]
        for food_key, rem_key in checks:
            rem = remaining.get(rem_key)
            val = f.get(food_key) or 0
            if rem is not None and val > rem:
                return False
        return True

    ideas = [f for f in foods if fits(f)][:8]
    return {
        "remaining": remaining,
        "ideas": ideas,
        "disclaimer": "Suggestions are informational only — follow your doctor's diet plan.",
    }
