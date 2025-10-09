from typing import Any
from pydantic_settings import BaseSettings
import os



class Settings(BaseSettings):
    app_name: str = "Assemblage API"
    db_host: str = os.getenv("DB_HOST", "assemblage-db")
    db_port: str = os.getenv("DB_PORT","5432")
    db_name: str = os.getenv("POSTGRES_DATABASE") # set error cathcing if any of thses are not set. they have to be in secrets.env otherwise db will break too
    db_user: str = os.getenv("POSTGRES_USER")
    db_pass: str = os.getenv("POSTGRES_PASSWORD")
    DATABASE_URL: str = f"postgresql+psycopg2://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
    mode: str = os.getenv(key="MODE", default="development")
    SAVE_ASSEMBLY: bool = (os.getenv("SAVE_ASSEMBLY", "true").lower() == "true")
    
    loggingConfig: dict[str, Any] = {
        'version': 1,
        'formatters': {'default': {
            'format': '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
        }},
        'handlers': {'wsgi': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://flask.logging.wsgi_errors_stream',
            'formatter': 'default'
        }},
        'root': {
            'level': 'INFO',
            'handlers': ['wsgi']
        }}
    

settings = Settings()