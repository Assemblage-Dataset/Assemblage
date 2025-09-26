from typing import Any
from pydantic_settings import BaseSettings
import os



class Settings(BaseSettings):
    app_name: str = "Assemblage API"
    db_host: str = os.getenv('MYSQL_HOST', 'assemblage-db')
    db_port: str = os.getenv('MYSQL_PORT','3306')
    db_name: str = os.getenv('MYSQL_DATABASE','assemblage')
    db_user_name: str = os.getenv('MYSQL_USER', 'assemblage')
    db_user_pass: str = os.getenv('MYSQL_PASSWORD', 'assemblage')
    DATABASE_URL: str = f"mysql+pymysql://{db_user_name}:{db_user_pass}@{db_host}:{db_port}/{db_name}"
    mode: str = os.getenv(key="MODE", default="development")
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