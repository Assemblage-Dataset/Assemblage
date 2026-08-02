"""Typed application settings (pydantic-settings v2).

Each worker builds its settings from environment variables at construction
time. Every legacy env-var name is preserved via ``validation_alias``;
``RABBITMQ_USER`` / ``RABBITMQ_PASS`` are the new (guest-default) MQ credential
names. Secrets are wrapped in ``SecretStr`` so they never leak into logs/repr.

There is deliberately **no import-time ``os.getenv``**: nothing is read until a
settings object is instantiated. The old ``config.py`` stays in place for the
un-ported workers and is removed in P8.
"""

import logging
import socket
from datetime import UTC, datetime
from platform import machine, system
from typing import Annotated

from pydantic import (
    AliasChoices,
    Field,
    SecretStr,
    computed_field,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from assemblage.enums import (
    IrScope,
    IrStage,
    RuntimeEnv,
    RustCodegenBackend,
    ScraperOutputPolicy,
    ScrapeSource,
    SupportedArchitecture,
    SupportedCompiler,
    SupportedLanguage,
    SupportedPlatform,
)

_ONE_YEAR_SECONDS = 60 * 60 * 24 * 31 * 12
_logger = logging.getLogger(__name__)


class MQSettings(BaseSettings):
    """RabbitMQ connection settings."""

    model_config = SettingsConfigDict(extra="ignore")

    host: str = Field(default="rabbitmq", validation_alias="MQ_HOST")
    port: int = Field(default=5672, validation_alias="MQ_PORT")
    user: str = Field(default="guest", validation_alias="RABBITMQ_USER")
    password: SecretStr = Field(default=SecretStr("guest"), validation_alias="RABBITMQ_PASS")


class DatabaseSettings(BaseSettings):
    """PostgreSQL connection settings (all required from the environment)."""

    model_config = SettingsConfigDict(extra="ignore")

    host: str = Field(validation_alias="DB_HOST")
    port: int = Field(validation_alias="DB_PORT")
    database: str = Field(validation_alias="POSTGRES_DATABASE")
    user: str = Field(validation_alias="POSTGRES_USER")
    password: SecretStr = Field(validation_alias="POSTGRES_PASSWORD")

    @property
    def url(self) -> str:
        return (
            f"postgresql+psycopg2://{self.user}:{self.password.get_secret_value()}"
            f"@{self.host}:{self.port}/{self.database}"
        )


class S3Settings(BaseSettings):
    """S3 / MinIO settings. S3 is 'enabled' iff a host is configured."""

    model_config = SettingsConfigDict(extra="ignore")

    host: str | None = Field(default=None, validation_alias="S3_HOST")
    access_key: str | None = Field(default=None, validation_alias="S3_ACCESS_KEY")
    secret_access_key: str | None = Field(default=None, validation_alias="S3_SECRET_ACCESS_KEY")
    port: int = Field(default=9000, validation_alias="S3_PORT")
    https: bool = Field(default=True, validation_alias="S3_HTTPS")
    region: str = Field(default="us-east-1", validation_alias="S3_REGION")

    @property
    def enabled(self) -> bool:
        return self.host is not None

    @model_validator(mode="after")
    def _require_credentials_when_enabled(self) -> "S3Settings":
        if self.enabled:
            missing = [
                name
                for name, value in (
                    ("S3_ACCESS_KEY", self.access_key),
                    ("S3_SECRET_ACCESS_KEY", self.secret_access_key),
                )
                if not value
            ]
            if missing:
                raise ValueError(f"S3_HOST is set ({self.host}) but missing: {missing}")
        return self


class WorkerSettings(BaseSettings):
    """Fields common to every worker process."""

    model_config = SettingsConfigDict(extra="ignore")

    runtime_env: RuntimeEnv = Field(default=RuntimeEnv.prod, validation_alias="RUNTIME_ENV")
    name: str = Field(default_factory=socket.gethostname, validation_alias="NAME")
    mq: MQSettings = Field(default_factory=MQSettings)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def log_level(self) -> str:
        return "DEBUG" if self.runtime_env == RuntimeEnv.dev else "INFO"


class CoordinatorSettings(WorkerSettings):
    """Coordinator process settings."""

    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    # Owners/repos never dispatched. A path, not a list of entries, because the
    # file is re-read on a live coordinator -- restarting it to change a config
    # value would strand the fleet until builders re-register. ./backend is
    # bind-mounted to /app, so this is backend/blocklist.txt on the host.
    blocklist_path: str = Field(default="/app/blocklist.txt", validation_alias="BLOCKLIST_PATH")


class BuilderSettings(WorkerSettings):
    """Builder process settings."""

    compiler: SupportedCompiler = Field(validation_alias=AliasChoices("compiler", "COMPILER"))
    language: SupportedLanguage = Field(validation_alias=AliasChoices("language", "LANGUAGE"))
    compiler_flag: str = Field(default="", validation_alias="COMPILER_FLAG")
    save_assembly: bool = Field(default=True, validation_alias="SAVE_ASSEMBLY")
    library: SupportedArchitecture = Field(
        default_factory=lambda: (
            SupportedArchitecture.X64 if "64" in machine() else SupportedArchitecture.X86
        )
    )
    build_os: SupportedPlatform = Field(default_factory=lambda: SupportedPlatform(system().lower()))
    s3: S3Settings = Field(default_factory=S3Settings)
    wait_for_build_opt_minutes: int = 5
    config_check_interval_s: int = 5
    binaries_root: str = "/binaries"
    # Rust-only knobs (ignored by the C/C++ Linux strategy). codegen_backend and
    # build_mode also flow into the builder's registration identity.
    codegen_backend: RustCodegenBackend = Field(
        default=RustCodegenBackend.LLVM,
        validation_alias=AliasChoices("codegen_backend", "CODEGEN_BACKEND"),
    )
    build_mode: str = Field(
        default="RelWithDebInfo",
        validation_alias=AliasChoices("build_mode", "BUILD_MODE"),
    )
    build_timeout_s: int = Field(default=1800, validation_alias="BUILD_TIMEOUT_S")
    cargo_home: str = Field(default="/cargo", validation_alias="CARGO_HOME")
    # DWARF extraction budgets. Wholly separate from build_timeout_s, which only
    # wraps the cargo/make invocations -- extraction runs after them and used to
    # be unbounded, which is what let one llvm/Debug binary hold a builder for
    # 18+ minutes (measured 2026-07-20). Enforced by dwarf.isolated in a child
    # process, because the extractor's own SIGALRM timeout cannot arm on the
    # Supervisor worker thread the builder runs tasks on.
    #
    # Per binary; on expiry that binary yields no debug info but is still stored.
    dwarf_timeout_s: int = Field(default=300, validation_alias="DWARF_TIMEOUT_S")
    # Across the whole extraction phase: a build emitting many binaries would
    # otherwise still park a builder for hours at dwarf_timeout_s each.
    dwarf_phase_timeout_s: int = Field(default=900, validation_alias="DWARF_PHASE_TIMEOUT_S")
    # RLIMIT_AS for the extractor child. Extraction costs ~42x the binary size,
    # so an unbounded run can OOM-kill the whole container; this kills the child.
    dwarf_mem_limit_mb: int = Field(default=8192, validation_alias="DWARF_MEM_LIMIT_MB")
    # Worker processes the extractor child shards compile units across. 95% of
    # extraction is pyelftools decoding DWARF byte-by-byte in pure Python, which
    # the GIL would serialise, so this has to be processes. Measured on a 248 MB
    # rust Debug binary (2026-07-20), output byte-identical at every setting:
    #   jobs=1 306s | jobs=2 167s (1.8x) | jobs=4 103s (3.0x) | jobs=8 76s (4.0x)
    # 4 is the knee: 75% parallel efficiency, and it matches CARGO_BUILD_JOBS so a
    # builder's extraction phase asks for no more cores than its compile phase
    # already does — 32 builders keep the same peak demand shape on 128 cores.
    dwarf_extract_jobs: int = Field(default=4, validation_alias="DWARF_EXTRACT_JOBS")
    # IR dumping (Rust only). OFF by default: emitting IR repartitions codegen units,
    # so an IR build's .text bytes differ from a non-IR build of the same source
    # (symbols/.rodata/.data/.eh_frame stay identical) -- a tier must opt in.
    ir_dump: bool = Field(default=False, validation_alias="IR_DUMP")
    ir_stages: str = Field(default="llvm-ir,mir", validation_alias="IR_STAGES")
    ir_scope: IrScope = Field(default=IrScope.REPO, validation_alias="IR_SCOPE")
    # Per-stage cap on the STORED (gzipped) tarball. A stage over it is dropped
    # whole and recorded in the manifest, never truncated.
    ir_max_bytes: int = Field(default=512 * 1024 * 1024, validation_alias="IR_MAX_BYTES")

    @property
    def ir_stage_list(self) -> list[IrStage]:
        """``IR_STAGES`` parsed; unknown names are dropped with a warning."""
        out: list[IrStage] = []
        for raw in self.ir_stages.split(","):
            name = raw.strip()
            if not name:
                continue
            try:
                out.append(IrStage(name))
            except ValueError:
                _logger.warning("ignoring unknown IR stage %r in IR_STAGES", name)
        return out


class ScraperSettings(WorkerSettings):
    """Scraper process settings."""

    git_token: SecretStr = Field(default=SecretStr(""), validation_alias="GITHUB_TOKEN")
    alternative_git_tokens: list[str] | None = None
    interval: int = Field(default=14400, validation_alias="SCRAPE_INTERVAL")
    default_start_time: int = Field(
        default_factory=lambda: int(datetime.now(UTC).timestamp()),
        validation_alias="SCRAPE_START_TIME",
    )
    default_end_time: int = Field(
        default_factory=lambda: int(datetime.now(UTC).timestamp()) - _ONE_YEAR_SECONDS,
        validation_alias="SCRAPE_END_TIME",
    )
    default_policy: ScraperOutputPolicy = Field(
        default=ScraperOutputPolicy.ON_REQUEST, validation_alias="SCRAPER_POLICY"
    )
    wait_for_config: bool = True
    # Comma-separated in the environment ("language:rust" or
    # "language:rust,stars:>10"); NoDecode skips pydantic-settings' JSON
    # parsing so the raw string reaches the validator below.
    qualifiers: Annotated[set[str], NoDecode] = Field(
        default_factory=lambda: {"language:c++"},
        validation_alias="SCRAPE_QUALIFIERS",
    )
    proxies: list[str] = Field(default_factory=list)
    source: ScrapeSource = Field(default=ScrapeSource.GITHUB, validation_alias="SCRAPE_DATASOURCE")

    @field_validator("qualifiers", mode="before")
    @classmethod
    def _split_qualifiers(cls, value: object) -> object:
        """Parse a comma-separated SCRAPE_QUALIFIERS string into a set."""
        if isinstance(value, str):
            return {part.strip() for part in value.split(",") if part.strip()}
        return value
