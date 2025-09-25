from typing import Union

from fastapi import FastAPI, APIRouter

from assemblage.api.routers import admin, control
app = FastAPI()
api_router = APIRouter(prefix="/api")

api_router.include_router(admin.router, prefix="/admin", tags=["admin"])


def print_routes():
    print("Registered routes:")
    for route in app.routes:
        print(f"Path: {route.path} | Name: {route.name} | Methods: {route.methods}")


print_routes()
@api_router.get("")
def read_root():
    return {"Hello1": "World"}


app.include_router(api_router)
