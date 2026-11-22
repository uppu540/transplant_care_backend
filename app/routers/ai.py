from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.ai.chat import run_chat
from app.auth import AuthUser, get_patient_user
from app.repository import MongoRepository, get_repository

router = APIRouter(prefix="/ai", tags=["ai"])


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    locale: str = "en"


@router.post("/chat")
async def chat(
    body: ChatBody,
    user: AuthUser = Depends(get_patient_user),
    repository: MongoRepository = Depends(get_repository),
):
    profile = await repository.get_profile_by_user_id(user.user_id)
    if not profile:
        raise HTTPException(404, "Profile not found")
    locale = body.locale or profile.get("locale") or "en"
    result = await run_chat(
        profile["id"],
        body.message,
        locale=locale,
        repository=repository,
    )
    result["disclaimer"] = (
        "TransplantCare does not replace medical care. "
        "Contact your transplant team for medical decisions."
    )
    return result
