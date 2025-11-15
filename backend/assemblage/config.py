from datetime import datetime, timezone
from typing import Any, Literal
from pydantic_settings import BaseSettings
from pydantic import computed_field, Field, model_validator
from platform import machine, system
import os
import socket
import logging

from assemblage.consts import RuntimeEnv, ScrapeSource

# set pika to only log warnings. otherwise it gets noisy - maybe this can be removed with better try except on all pika ops
logging.getLogger("pika").setLevel(logging.WARNING)

# dotenv.load_dotenv()


class S3Settings(BaseSettings):
    S3_HOST: str | None = None # if s3 host is set then we treat s3 as enabled
    S3_ACCESS_KEY: str | None = None
    S3_SECRET_ACCESS_KEY: str | None = None
    S3_PORT: int = 9000
    S3_HTTPS: bool  = True
    S3_REGION: str  = "us-east-1" # maybe do enum, but fine for

    @property
    def s3_enabled(self) -> bool:
        """Check if S3 mode is considered enabled"""
        return self.S3_HOST is not None

    @model_validator(mode="after")
    def validate_s3_fields(cls, values):
        # Convert values dict to an object for property access        
        if values.s3_enabled:
            missing = []
            if not values.S3_ACCESS_KEY:
                missing.append("S3_ACCESS_KEY") 
                pass
            if not values.S3_SECRET_ACCESS_KEY: 
                missing.append("S3_SECRET_ACCESS_KEY") 
            if missing:
                raise ValueError(f"S3 HOST is set {values.S3_HOST} but missing required fields: {missing}")

        return values
    
class AssemblageSettings(BaseSettings):
    """
    Core env variables and settings
    """
    app_name: str = "Assemblage"
    runtime_env: RuntimeEnv = Field(default=RuntimeEnv.prod, env="ENV")
    mq_host: str = Field(default="rabbitmq", env="MQ_HOST")
    mq_port: int = Field(default=5672, env="MQ_PORT")
    name: str = Field(default_factory=lambda: os.getenv(
        "NAME") or socket.gethostname())
    
    # is there a way to delete 
    

    @computed_field
    @property
    def logLevel(self) -> Literal['DEBUG'] | Literal['INFO']:
        # i would rather set using 
        return 'DEBUG' if self.runtime_env == RuntimeEnv.dev else 'INFO'
    def __str__(self) -> str:
        items = self.model_dump()
        return f"{self.__class__.__name__}:\n" + "\n".join(
            f"  {key}: {value}" for key, value in items.items()
        )
class CoordinatorSettings(AssemblageSettings, S3Settings):
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
    cluster_name: str = Field("CLUSTER_NAME") # eventually use the website host name for DNS for this

    mq_manage_port: int = Field(default=56723)

    @computed_field
    @property
    def databaseURL(self) -> str:
        return f"postgresql+psycopg2://{self.db_user}:{self.db_pass}@{self.db_host}:{self.db_port}/{self.db_name}"


class ScraperSettings(AssemblageSettings):
    """
    Scraper specific settings
    """
    #git_token: str = Field(..., env="GITHUB_TOKEN")   # ideal, with dotenv
    git_token: str = Field(os.getenv("GITHUB_TOKEN"))   # not lovin' this, but it DOES prevent dotenv dependency
    start_time: int = Field(os.getenv("SCRAPE_START_TIME", default=int(datetime.now(timezone.utc).timestamp()))) # default is now
    end_time: int = Field(os.getenv("SCRAPE_END_TIME", int(datetime.now(timezone.utc).timestamp())-60*60*24*31*12))# default is now - 1 year ish
    interval: int = Field(os.getenv("SCRAPE_INTERVAL", 14400))
    source: ScrapeSource = Field(os.getenv("SCRAPE_DATASOURCE", default=ScrapeSource.GITHUB))


class BuilderSettings(AssemblageSettings, S3Settings):
    """
    Builder specific settings
    This populates
    """
    save_assembly: bool = Field(
        # possibly should be sent via command from coordinator to make it dynamic, but thats later...
        default=True, env="SAVE_ASSEMBLY")
    # detect what platform ( linux, windows, darwin) teh builder is running on. for now just needed in builder
    # not quite perfect but should do for now    platform: str = Field(default_factory=lambda: platform.system().lower())
    library: str = Field(
        default_factory=lambda: "x64" if '64' in machine() else 'x86')
    build_os: str = Field(default_factory=lambda: system().lower())
    compiler: str
    language: str
    build_mode: str = Field(default="Release", env="BUILD_MODE")
