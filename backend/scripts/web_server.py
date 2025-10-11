from fastapi import FastAPI, APIRouter
from functools import lru_cache
import os


from assemblage.api.routers import admin, control
from assemblage.config import Settings

@lru_cache
def get_settings():
    return Settings()


app = FastAPI()
api_router = APIRouter(prefix="/api")

api_router.include_router(admin.router, prefix="/admin", tags=["admin"])


@app.get("healthcheck")
def read_root():
    return {"server": "healthy"}


app.include_router(api_router)
