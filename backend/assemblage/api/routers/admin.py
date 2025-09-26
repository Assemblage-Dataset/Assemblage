# 3rd parties
from typing import Annotated
from fastapi import Depends, Query, APIRouter
from sqlmodel import Session, select

# local
from assemblage.database.db import get_session
from assemblage.database.models import Status


router = APIRouter()



@router.get("", tags=["admin"])
async def read_users():
    return [{"username": "Rick"}, {"username": "Morty"}]


@router.get("/health")
async def check_server_health( session: Annotated[Session, Depends(get_session)], offset: int = 0,
    limit: Annotated[int, Query(le=100)] = 100,
                              ) :
    '''
    Checks server health of entire docker stack - could add a check for frontend too?
    '''
    return session.exec(select(Status).offset(offset).limit(limit)).all()

# def read_heroes(
#     session: SessionDep,
#     offset: int = 0,
#     limit: Annotated[int, Query(le=100)] = 100,
# ) -> list[Hero]:
#     heroes = session.exec(select(Hero).offset(offset).limit(limit)).all()
#     return heroes


@router.get("/health/db")
async def check_db_health():
    '''
    returns health of database
    '''
    return


@router.get("/health/coord")
async def check_coord_health():
    '''
    returns health of coordinator
    '''
    return


@router.get("/health/builder")
async def check_builder_health():
    '''
    returns health of builder
    '''
    return


@router.get("/health/scraper")
async def check_scraper_health():
    '''
    returns health of scraper
    '''
    return
