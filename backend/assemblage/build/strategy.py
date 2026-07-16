"""The build-strategy interface and its platform factory.

``BuildStrategy`` is a slim ABC: it declares the identity a build reports
(platform, compiler, language, versions) and the three lifecycle steps the
pipeline drives — ``prepare`` (pre-build config), ``build`` (compile), and
``debug_info`` (DWARF extraction). Beyond the minimal identity fields it also
exposes ``build_mode`` and ``base_path`` (the frozen ``Build_mode`` metadata key
and the clone/own-dir root both need a typed home) and ``own_dir`` (ownership
fix-up the artifact saver invokes on locally-written binaries).

``make_strategy`` is the one spot that knows platforms: Linux -> the in-tree
:class:`~assemblage.build.linux.LinuxBuildStrategy`; Windows -> a **lazy** import
of the quarantined ``assemblage.legacy.windows.strategy`` (the only core
reference to the frozen legacy package).
"""

import logging
from abc import ABC, abstractmethod

from assemblage.enums import BuildStatus, SupportedLanguage, SupportedPlatform
from assemblage.settings import BuilderSettings

logger = logging.getLogger(__name__)


class BuildStrategy(ABC):
    """A platform's clone-config-build-extract behaviour."""

    platform: str
    compiler: str
    language: str
    compiler_version: str | None
    toolset_version: str | None
    build_mode: str
    base_path: str

    @abstractmethod
    def prepare(self, clone_dir: str, compiler_flag: str) -> object | None:
        """Pre-build configuration; return an opaque token passed to ``build``."""

    @abstractmethod
    def build(
        self, clone_dir: str, compiler_flag: str, prepared: object | None
    ) -> tuple[str, BuildStatus]:
        """Compile the project; return (combined output, status)."""

    @abstractmethod
    def debug_info(self, clone_dir: str, original_files: list[str]) -> list[dict[str, object]]:
        """Extract per-binary DWARF ``Binary_info_list`` items for freshly built files."""

    @abstractmethod
    def find_binaries(self, path: str) -> set[str]:
        """Find the built binaries (and optional assembly artifacts) under ``path``."""

    def own_dir(self, path: str) -> None:
        """Fix up ownership of a produced directory (no-op unless overridden)."""
        return None


def make_strategy(settings: BuilderSettings) -> BuildStrategy:
    """Construct the build strategy for the configured platform."""
    if settings.build_os == SupportedPlatform.LINUX:
        if settings.language == SupportedLanguage.RUST:
            from assemblage.build.rust import RustBuildStrategy

            return RustBuildStrategy(settings)

        from assemblage.build.linux import LinuxBuildStrategy

        return LinuxBuildStrategy(settings)

    if settings.build_os == SupportedPlatform.WINDOWS:
        # The single, lazy reference to the frozen Windows/MSVC quarantine.
        from assemblage.legacy.windows.strategy import WindowsDefaultStrategy

        strategy: BuildStrategy = WindowsDefaultStrategy(
            compiler=settings.compiler,
            language=settings.language,
            library=settings.library,
            save_assembly=settings.save_assembly,
        )
        return strategy

    raise ValueError(f"unsupported build platform: {settings.build_os}")
