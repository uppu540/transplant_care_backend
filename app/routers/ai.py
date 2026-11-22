from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.ai.chat import run_chat
from app.auth import AuthUser, get_current_user
from app.db import get_profile_by_user_id

router = APIRouter(prefix="/ai", tags=["ai"])


class ChatBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    locale: str = "en"


@router.post("/chat")
async def chat(body: ChatBody, user: AuthUser = Depends(get_current_user)):
    profile = get_profile_by_user_id(user.user_id)
    locale = body.locale or profile.get("locale") or "en"
    result = await run_chat(profile["id"], body.message, locale=locale)
    result["disclaimer"] = (
        "TransplantCare does not replace medical care. "
        "Contact your transplant team for medical decisions."
    )
    return result
