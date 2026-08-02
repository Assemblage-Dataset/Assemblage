"""The Linux (gcc/clang) build strategy.

Ported from ``LinuxBuildStrategy`` in the old ``build_method.py``. The compiler
version detection, the build-command generation (bootstrap / configure / cmake /
make, the hardcoded ``RelWithDebInfo`` ``-g -DNDEBUG``, the CFLAGS/CXXFLAGS and
cmake ``_RELWITHDEBINFO`` overrides, ``-save-temps=obj`` and ``timeout 10m
make -j16``) and the ownership fix-up are byte-identical to the original — the
E2E golden pins them. Subprocess execution now goes through
:func:`assemblage.build.commands.run_command`, which carries the P5
``start_new_session`` fix so a build timeout can never SIGTERM the worker.
"""

import glob
import logging
import os
import re
import tempfile

from assemblage.build.commands import run_command
from assemblage.build.detect import get_build_system
from assemblage.build.discovery import find_binaries
from assemblage.build.strategy import BuildStrategy
from assemblage.dwarf.isolated import extract_each
from assemblage.enums import BuildStatus
from assemblage.settings import BuilderSettings

logger = logging.getLogger(__name__)

BUILD_MODE = "RelWithDebInfo"
_BUILD_TIMEOUT_S = 600.0


def work_base_path(settings: BuilderSettings) -> str:
    """Where projects are cloned and built: a tempdir under S3, else the bin root."""
    return tempfile.gettempdir() if settings.s3.enabled else settings.binaries_root


class LinuxBuildStrategy(BuildStrategy):
    def __init__(self, settings: BuilderSettings, num_p_job: int = 16) -> None:
        self.platform = "linux"
        self.compiler = str(settings.compiler)
        self.language = str(settings.language)
        self.save_assembly = settings.save_assembly
        self.num_p_job = num_p_job
        self.build_mode = BUILD_MODE
        self.toolset_version: str | None = None
        self.base_path = work_base_path(settings)
        # Extraction budgets, enforced out-of-process (see dwarf.isolated).
        self.dwarf_timeout_s = settings.dwarf_timeout_s
        self.dwarf_phase_timeout_s = settings.dwarf_phase_timeout_s
        self.dwarf_mem_limit_bytes = settings.dwarf_mem_limit_mb * 1024 * 1024
        self.dwarf_extract_jobs = settings.dwarf_extract_jobs
        self.compiler_version = self._get_compiler_version()

        try:
            perms = os.stat(self.base_path)
            self.output_dir_uid = perms.st_uid
            self.output_dir_gid = perms.st_gid
        except OSError:
            self.output_dir_uid = 0
            self.output_dir_gid = 0

    def _get_compiler_version(self) -> str | None:
        """Detect gcc/clang version via ``-dumpfullversion -dumpversion``."""
        try:
            result = run_command(f"{self.compiler} -dumpfullversion -dumpversion", timeout=60)
            if result.returncode == 0:
                output = result.stdout.decode(errors="ignore").strip()
                match = re.search(r"\d+(\.\d+)+", output)
                if match:
                    return match.group(0)
                raise ValueError(f"Failed to get compiler version: {output}")
        except Exception as e:
            logger.warning("Failed to get compiler version: %s", e)
        return None

    def prepare(self, clone_dir: str, compiler_flag: str) -> object | None:
        """Linux needs no pre-build config."""
        return None

    def build(
        self, clone_dir: str, compiler_flag: str, prepared: object | None
    ) -> tuple[str, BuildStatus]:
        """Generate and run the build command for the repo's build system."""
        files = [
            filename.split("/")[-1] for filename in glob.iglob(clone_dir + "**/**", recursive=True)
        ]
        logger.debug("%s files in repo %s", len(files), clone_dir)

        build_tool = get_build_system(files)

        # Hardcoded RelWithDebInfo: -g (debug info) + -DNDEBUG (assertions off).
        debug_flag = "-g"
        ndebug_flag = "-DNDEBUG"
        cmake_build_type = BUILD_MODE

        base_flags = " ".join(f for f in [debug_flag, ndebug_flag, compiler_flag] if f)
        save_temps = "-save-temps=obj" if self.save_assembly else ""
        all_flags = " ".join(f for f in [base_flags, save_temps] if f)

        cflags = f"$CFLAGS {all_flags}"
        cxxflags = f"$CXXFLAGS {all_flags}"
        extra_flags = f'CFLAGS="{cflags}" CXXFLAGS="{cxxflags}"'
        # Override both CMAKE_C_FLAGS and the build-type-specific flags so the
        # build-type defaults can't append a conflicting -O after ours.
        bt_upper = cmake_build_type.upper()
        cmake_flags = (
            f'-DCMAKE_BUILD_TYPE="{cmake_build_type}" '
            f'-DCMAKE_C_FLAGS="{all_flags}" '
            f'-DCMAKE_CXX_FLAGS="{all_flags}" '
            f'-DCMAKE_C_FLAGS_{bt_upper}="{all_flags}" '
            f'-DCMAKE_CXX_FLAGS_{bt_upper}="{all_flags}"'
        )

        cmd = ""
        if "bootstrap" in build_tool:
            cmd = (
                f"cd {clone_dir} && ./bootstrap && "
                f"bash ./configure {extra_flags} && timeout 10m make {extra_flags} -j{self.num_p_job}"
            )
        elif "configure" in build_tool:
            cmd = (
                f"cd {clone_dir} && bash ./configure {extra_flags} && "
                f"timeout 10m make {extra_flags} -j{self.num_p_job}"
            )
        elif "cmake" in build_tool:
            cmd = (
                f"cd {clone_dir} && cmake -Bbuild ./ {cmake_flags} && cd build && "
                f"timeout 10m make -j{self.num_p_job}"
            )
        elif "make" in build_tool:
            cmd = f"cd {clone_dir} && timeout 10m make {extra_flags} -j{self.num_p_job}"
        logger.debug("Linux cmd generated: %s", cmd)

        if cmd == "":
            logger.warning("No build command created for linux")
            return "No Build Command Made", BuildStatus.FAILED

        result = run_command(cmd, timeout=_BUILD_TIMEOUT_S)
        status = BuildStatus.SUCCESS if result.returncode == 0 else BuildStatus.FAILED
        self.own_dir(os.path.dirname(clone_dir))
        return result.stdout.decode() + result.stderr.decode(), status

    def find_binaries(self, path: str) -> set[str]:
        """Find built binaries under ``path`` for this platform / assembly setting."""
        return find_binaries(path, platform=self.platform, save_assembly=self.save_assembly)

    def debug_info(self, clone_dir: str, original_files: list[str]) -> list[dict[str, object]]:
        """Extract DWARF info from the binaries built under ``clone_dir``."""
        bin_files = {f for f in self.find_binaries(clone_dir) if f not in original_files}
        if not bin_files:
            return []

        # No source_root: the C golden is pinned against the un-remapped output.
        # Same extractor, same arguments -- only the process it runs in changed.
        items: list[dict[str, object]] = list(
            extract_each(
                bin_files,
                timeout_secs=self.dwarf_timeout_s,
                phase_timeout_s=self.dwarf_phase_timeout_s,
                mem_limit_bytes=self.dwarf_mem_limit_bytes,
                jobs=self.dwarf_extract_jobs,
            )
        )
        if not items:
            logger.info("No DWARF debug info found in any binary")
        return items

    def own_dir(self, path: str) -> None:
        """Chown a produced tree to the base-path owner (a container permissions fix-up)."""
        for root, dirs, files in os.walk(path):
            for name in (*dirs, *files):
                try:
                    os.chown(os.path.join(root, name), self.output_dir_uid, self.output_dir_gid)
                except OSError:
                    pass
        try:
            os.chown(path, self.output_dir_uid, self.output_dir_gid)
        except OSError:
            pass
