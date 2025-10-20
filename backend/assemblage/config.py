from typing import Any
from .consts import RuntimeEnv
from pydantic_settings import BaseSettings
from pydantic import computed_field, Field
from platform import machine, system
import os


class AssemblageSettings(BaseSettings):
    """
    Core env variables and settings
    """
    app_name: str = "Assemblage"
    runtime_env: RuntimeEnv = Field(default=RuntimeEnv.prod, env="ENV")
    mq_host: str = Field(default="rabbitmq", env="MQ_HOST")
    mq_port: int = Field(default=5672, env="MQ_PORT")

    @computed_field
    @property
    def loggingConfig(self) -> dict[str, Any]:
        log_level = 'DEBUG' if self.runtime_env == RuntimeEnv.dev else 'INFO'
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
            }
        }


class CoordinatorSettings(AssemblageSettings):
    """
    Coordinator specific settings
    """
    db_host: str = Field(os.getenv('DB_HOST'))
    db_port: int = Field(os.getenv('DB_PORT'))
    db_name: str = Field(os.getenv('POSTGRES_DATABASE'))
    db_user: str = Field(os.getenv("POSTGRES_USER"))
    db_pass: str = Field(os.getenv("POSTGRES_PASSWORD"))

    # extracted directly from coordinator
    reproduce_mode: int = Field(0)
    aws_mode: int = Field(0)
    cluster_name:str = Field("ClusterName")

    mq_manage_port: int = Field(default=56723)

    @computed_field
    @property
    def databaseURL(self) -> str:
        return f"postgresql+psycopg2://{self.db_user}:{self.db_pass}@{self.db_host}:{self.db_port}/{self.db_name}"


class ScraperSettings(AssemblageSettings):
    """
    Scraper specific settings
    """
    git_token: str = Field(..., env="GITHUB_TOKEN")


class BuilderSettings(AssemblageSettings):
    """
    Builder specific settings
    This populates
    """
    SAVE_ASSEMBLY: bool = Field(default=True, env="SAVE_ASSEMBLY") # possibly should be sent via command from coordinator to make it dynamic, but thats later...
    # detect what platform ( linux, windows, darwin) teh builder is running on. for now just needed in builder 
    library: str = Field(default_factory=lambda: "x64" if '64' in machine() else 'x86') # not quite perfect but should do for now    platform: str = Field(default_factory=lambda: platform.system().lower()) 
    build_os: str = Field(default_factory=lambda: system().lower()) 
    compiler:str = Field(os.getenv("COMPILER")) # what compiler the image is using # could enum thishow 
    language:str = Field(os.getenv("LANGUAGE")) # what langauge ie c++  # could also enum this...

