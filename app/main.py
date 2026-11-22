from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import httpx

from app.config import get_settings
from app.routers import ai, emergency, food, labs, me, medicines

settings = get_settings()

app = FastAPI(
    title="TransplantCare API",
    description="AI, SOS, and food/lab helpers for TransplantCare. Does not replace medical care.",
    version="0.1.0",
)

origins = (
    ["*"]
    if settings.cors_origins.strip() == "*"
    else [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(me.router)
app.include_router(medicines.router)
app.include_router(food.router)
app.include_router(labs.router)
app.include_router(ai.router)
app.include_router(emergency.router)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "transplantcare",
        "disclaimer": "Does not replace medical care",
    }


@app.get("/health/supabase")
async def check_supabase_connection():

    supabase_url = settings.supabase_url
    supabase_key = settings.supabase_anon_key

    if not supabase_url or not supabase_key:
        raise HTTPException(
            status_code=500,
            detail="Supabase configuration is missing"
        )

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{supabase_url.rstrip('/')}/rest/v1/",
                headers={
                    "apikey": supabase_key
                }
            )

        return {
            "status": "success" if response.status_code == 200 else "error",
            "supabase_http_status": response.status_code,
            "supabase_connected": response.status_code == 200
        }

    except httpx.RequestError as error:
        raise HTTPException(
            status_code=503,
            detail=f"Supabase connection failed: {type(error).__name__}"
        )


@app.get("/health/supabase/auth")
async def check_supabase_auth():

    supabase_url = settings.supabase_url
    supabase_key = settings.supabase_anon_key

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(
                f"{supabase_url.rstrip('/')}/auth/v1/settings",
                headers={
                    "apikey": supabase_key
                }
            )

        return {
            "status": "success" if response.status_code == 200 else "error",
            "auth_http_status": response.status_code,
            "authentication_service_reachable": response.status_code == 200
        }

    except httpx.RequestError as error:
        raise HTTPException(
            status_code=503,
            detail=f"Supabase auth connection failed: {type(error).__name__}"
        )
