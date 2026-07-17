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
    RUST = "rust"


class SupportedCompiler(StrEnum):
    CLANG = "clang"
    GCC = "gcc"
    MSVC = "MSVC"
    RUSTC = "rustc"


class RustCodegenBackend(StrEnum):
    """rustc ``-Zcodegen-backend`` selection (one buildopt column per backend).

    Stored lowercase in ``buildopt.codegen_backend`` (native C/C++ toolchains
    keep the column's ``''`` default). The Rust build strategy (R2) consumes
    these; they land with the schema so the enum <-> column story is complete.
    """

    LLVM = "llvm"
    CRANELIFT = "cranelift"
    GCC = "gcc"


class IrStage(StrEnum):
    """A compiler IR the Rust worker can dump for a build.

    rustc lowers AST -> HIR -> THIR -> MIR -> (monomorphize) -> LLVM-IR -> machine
    code; ``asm`` is the textual form of the last step. GIMPLE and CLIF are the
    *backend-native* IRs of cg_gcc and cranelift, which never coexist with LLVM-IR
    in one build -- which IR a build can produce is a property of its codegen
    backend, so see :class:`assemblage.build.rust.IrCaps`.

    Values are lowercase (wire convention); the DB stores the member NAME.
    Verified against nightly-2026-06-15 (2026-07-17), see
    ``backend/assemblage/builder/ir.py`` for how each is obtained.
    """

    AST = "ast"
    HIR = "hir"
    THIR = "thir"
    MIR = "mir"
    LLVM_IR = "llvm-ir"
    ASM = "asm"
    GIMPLE = "gimple"
    CLIF = "clif"

    @property
    def rides_along(self) -> bool:
        """True when the stage is a ``--emit`` kind, dumped by the normal build.

        The rest need a *separate* ``-Zunpretty`` pass, which produces no object
        file -- so they cost a second compile rather than a bigger one.
        """
        return self in (IrStage.MIR, IrStage.LLVM_IR, IrStage.ASM)

    @property
    def emit_kind(self) -> str | None:
        """The ``--emit=`` kind for a ride-along stage, else ``None``."""
        match self:
            case IrStage.MIR:
                return "mir"
            case IrStage.LLVM_IR:
                return "llvm-ir"
            case IrStage.ASM:
                return "asm"
            case _:
                return None

    @property
    def unpretty_mode(self) -> str | None:
        """The ``-Zunpretty=`` mode for a separate-pass stage, else ``None``."""
        match self:
            case IrStage.AST:
                return "ast-tree"
            case IrStage.HIR:
                return "hir-tree"
            case IrStage.THIR:
                return "thir-tree"
            case _:
                return None

    @property
    def file_suffix(self) -> str:
        """The on-disk extension cargo/rustc gives this stage's dump."""
        match self:
            case IrStage.LLVM_IR:
                return ".ll"
            case IrStage.MIR:
                return ".mir"
            case IrStage.ASM:
                return ".s"
            case IrStage.GIMPLE:
                return ".c"  # cg_gcc dumps libgccjit's C-like reproducer
            case IrStage.CLIF:
                return ".clif"
            case _:
                return ".txt"  # -Zunpretty stages: we name the file ourselves


class IrScope(StrEnum):
    """Which crates' IR to keep.

    A real 67-crate repo emits ~350 MB of IR at RelWithDebInfo, of which **93% is
    dependency crates** (measured 2026-07-17) -- crates.io code that is identical
    across every repo depending on it. ``REPO`` keeps only the repo's own crates
    (~19 MB raw / ~2 MB gzipped), mirroring the ``origin: in_repo`` split the
    metadata already draws.
    """

    REPO = "repo"
    ALL = "all"


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
