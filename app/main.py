from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.mongo import close_mongo, connect_mongo
from app.routers import ai, auth, emergency, food, labs, me, medicines

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await connect_mongo()
    try:
        yield
    finally:
        await close_mongo()


app = FastAPI(
    title="TransplantCare API",
    description="AI, SOS, and food/lab helpers for TransplantCare. Does not replace medical care.",
    version="0.1.0",
    lifespan=lifespan,
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
app.include_router(auth.router)
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


