"""Locate built binaries (and optional assembly artifacts) in a work tree.

``find_binaries`` is the pre-re-architecture ``BuildStrategy.find_binaries``,
lifted to a free function parameterised on ``platform`` and ``save_assembly``.
The skip-directory list, the extension whitelist and the ``-save-temps`` ``.s``
inclusion are preserved exactly — the E2E golden pins the ``.s`` files as
``binaries`` rows, so any change here is observable.
"""

import logging
import os

import pefile
from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

logger = logging.getLogger(__name__)

# Directories that tend to hold pre-existing binaries (vendored code, packaging
# artifacts, test fixtures) rather than things we built — skipped wholesale.
_SKIP_DIRS = frozenset(
    {
        ".git",
        "third_party",
        "3rdparty",
        "thirdparty",
        "vendor",
        "external",
        "deps",
        "debian",
        "node_modules",
        ".cache",
        "testdata",
        "test_data",
    }
)

_BINARY_EXTS = (".pdb", ".exe", ".dll", ".lib")
_ASSEMBLY_EXTS = (".s", ".ii", ".bc", ".S", ".obj", ".asm", ".cod")


def is_elf_executable(path: str) -> bool:
    """Whether ``path`` is an ELF ``ET_EXEC``/``ET_DYN`` file (the linux magic
    check ``find_binaries`` applies inline). Used by the Rust strategy's
    cargo-JSON fallback walk so both discovery paths agree on what "a binary" is.
    """
    try:
        with open(path, "rb") as f:
            return ELFFile(f).header["e_type"] in ("ET_EXEC", "ET_DYN")
    except (OSError, ELFError):
        return False


def find_binaries(path: str, *, platform: str, save_assembly: bool) -> set[str]:
    """Find ELF/PE executables and (optionally) assembly artifacts under ``path``."""
    logger.info("Finding executables in %s, saving assembly files too: %s", path, save_assembly)
    file_paths: set[str] = set()

    for root, dirs, file_names in os.walk(os.path.realpath(path)):
        dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]

        for file_name in file_names:
            location = f"{root}/{file_name}"
            location_lc = location.lower()
            if not os.path.exists(location):
                continue
            try:
                if save_assembly and location_lc.endswith(_ASSEMBLY_EXTS):
                    file_paths.add(location)
                    continue

                if file_name == "pdbinfo.json":
                    file_paths.add(location)
                    continue

                if location_lc.endswith(_BINARY_EXTS) and os.path.isfile(location):
                    file_paths.add(location)
                    continue

                with open(location, "rb") as f:
                    if platform == "linux":
                        try:
                            ef = ELFFile(f)
                            if ef.header["e_type"] in ("ET_EXEC", "ET_DYN"):
                                file_paths.add(location)
                        except ELFError:
                            continue
                    elif platform == "windows":
                        try:
                            pefile.PE(location)
                            file_paths.add(location)
                        except pefile.PEFormatError:
                            continue
            except OSError:
                continue

    logger.debug("Found executables: %s", file_paths)
    return file_paths
