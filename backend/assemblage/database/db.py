from sqlmodel import create_engine, Session
from assemblage.config import Settings


# connect_args = {"check_same_thread": False}
engine = create_engine(Settings().DATABASE_URL) # 

# for fastAPI only. 
def get_session():
    with Session(engine) as session:
        yield session
        session.commit()



