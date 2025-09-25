from fastapi import APIRouter

router = APIRouter()


@router.get("", tags=["admin"])
async def read_users():
    return [{"username": "Rick"}, {"username": "Morty"}]


@router.get("/users/me", tags=["admin"])
async def read_user_me():
    return {"username": "fakecurrentuser"}


@router.get("/users/{username}", tags=["admin"])
async def read_user(username: str):
    return {"username": username}