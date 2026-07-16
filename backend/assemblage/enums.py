"""Enumerations shared across the Assemblage system.

Every string enum preserves the two frozen conventions:

- the **RabbitMQ wire format** carries the lowercase member ``.value``
  (``"success"``) — see ``tests/fixtures/messages/enum_wire_values.json``;
- the **database** stores the uppercase member ``.name`` (``"SUCCESS"``) via
  SQLAlchemy's non-native ``Enum`` type — see ``backend/alembic/README.md``.

``StrEnum`` gives the ``(str, Enum)`` semantics (members compare/serialize as
their lowercase value); the status enums additionally keep ``__str__ -> .name``
to match the pre-re-architecture string form used in logs.
"""

from enum import Enum, StrEnum


class RuntimeEnv(StrEnum):
    dev = "development"
    prod = "production"


class WorkerType(StrEnum):
    Coordinator = "coordinator"
    Builder = "builder"
    Scraper = "scraper"
    LegacyConan = "legacy_conan"


class BuildStatus(StrEnum):
    """Clone and build status codes (all members kept for DB parity)."""

    INIT = "init"
    PROCESSING = "processing"
    FAILED = "failed"
    SUCCESS = "success"
    TIMEOUT = "timeout"
    BLACKLIST = "blacklist"
    OUTDATED_MSG = "outdated_msg"  # a message overtime, not build overtime
    EXCLUDE = "exclude"
    COMMAND_FAILED = "command_failed"

    def __str__(self) -> str:
        return self.name


class CloneStatus(StrEnum):
    NOT_STARTED = "not_started"
    PROCESSING = "processing"
    FAILED = "failed"
    SUCCESS = "success"
    TIMEOUT = "timeout"
    COMMAND_FAILED = "command_failed"

    def __str__(self) -> str:
        return self.name


class PriorityStatus(StrEnum):
    LOW = "low"
    MID = "medium"
    HIGH = "high"

    def __str__(self) -> str:
        return self.name


class ScrapeSource(StrEnum):
    """A valid source of scraped data (currently just GitHub)."""

    GITHUB = "github"

    def __str__(self) -> str:
        return self.name


class ScraperMsgType(StrEnum):
    SETUP = "setup"
    UPDATE = "update"  # update policy/configurations
    REQUEST_REPOS = "request_repos"  # trigger sending when policy is ON_REQUEST


class ScraperOutputPolicy(StrEnum):
    """How the scraper decides to send collected repositories to the coordinator."""

    CONTINUOUS = "continuous"  # send whenever a bundle is filled
    ON_REQUEST = "on_request"  # fill a bundle then wait for a REQUEST_REPOS broadcast


class GithubTimeOrder(StrEnum):
    """How the scraper's GitHub queries are sorted."""

    CREATED = "created"
    PUSHED = "pushed"


class SupportedPlatform(StrEnum):
    WINDOWS = "windows"
    LINUX = "linux"


class SupportedLanguage(StrEnum):
    CPP = "c++"


class SupportedCompiler(StrEnum):
    CLANG = "clang"
    GCC = "gcc"
    MSVC = "MSVC"


class SupportedArchitecture(StrEnum):
    X64 = "x64"
    X86 = "x86"


class OptLevel(Enum):
    """Optimization levels, translated to per-toolchain flags."""

    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3

    def __str__(self) -> str:
        return self.to_gnu_opt()

    def to_msvc_opt(self) -> str:
        """MSVC optimization flags."""
        match self:
            case OptLevel.LOW:
                return "/O1"
            case OptLevel.MEDIUM:
                return "/O2"
            case OptLevel.HIGH:
                return "/Ox"
            case _:
                return "/Od"

    def to_gnu_opt(self) -> str:
        """clang/gcc optimization flags."""
        match self:
            case OptLevel.LOW:
                return "-O1"
            case OptLevel.MEDIUM:
                return "-O2"
            case OptLevel.HIGH:
                return "-O3"
            case _:
                return "-O0"
