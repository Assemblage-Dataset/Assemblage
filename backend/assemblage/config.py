from typing import Any
from pydantic_settings import BaseSettings
from pydantic import computed_field
import os



class AssemblageSettings(BaseSettings):
    '''
    Core env variables and settings
    
    '''
    app_name: str = "Assemblage"
    mode: str = os.getenv(key="MODE", default="development")    
    mq_host: str = os.getenv("MQ_HOST", default="rabb")
    mq_port: str = os.getenv("MQ_PORT", default="5672")

    @computed_field
    def loggingConfig(self)->dict[str, Any]:
    # not sure this is working
        log_level = 'DEBUG' if self.mode == 'development' else 'INFO'
        return {
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
                'level': log_level,
                'handlers': ['wsgi']
            }}

class CoordinatorSettings(AssemblageSettings):
    '''
    Coordinator specific settings - potentially backend will need to inherit this too/ just copy the db
    
    '''
    db_host: str = os.getenv("DB_HOST", "assemblage-db")
    db_port: str = os.getenv("DB_PORT","5432")
    db_name: str = os.getenv("POSTGRES_DATABASE") # set error cathcing if any of thses are not set. they have to be in secrets.env otherwise db will break too
    db_user: str = os.getenv("POSTGRES_USER")
    db_pass: str = os.getenv("POSTGRES_PASSWORD")
    def databaseURL(self)->str:
        return f"postgresql+psycopg2://{self.db_user}:{self.db_pass}@{self.db_host}:{self.db_port}/{self.db_name}" 
    mq_manage_port: int = 56723

class ScraperSettings(AssemblageSettings):
    '''
    Scraper specific settings
    '''
    git_token: str = os.getenv("GITHUB_TOKEN")
    
    
class BuilderSettings(AssemblageSettings):
    '''
    Builder specific settings
    '''
    SAVE_ASSEMBLY: bool = (os.getenv("SAVE_ASSEMBLY", "true").lower() == "true")
    
    

