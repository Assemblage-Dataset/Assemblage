"""Build-system detection from a repository's file list.

``get_build_system`` is the pre-re-architecture ``analyze.analyze`` function,
moved here verbatim. The dead, ``print``-ing ``extract_ELF_debug_line_info`` was
dropped (the real DWARF extraction lives in :mod:`assemblage.dwarf.extract`).
"""

from collections.abc import Iterable

_BUILD_SYSTEMS: dict[str, list[str]] = {
    "make": ["makefile", "makefile.in", "makefile.am"],
    "cmake": ["cmakelists.txt"],
    "sln": [".sln"],
    "autoconf": ["configure", "configure.ac", "configure.in"],
    "bootstrap": ["bootstrap", "bootstrap.sh", "autogen.sh"],
    "meson": ["meson.build"],
    "ninja": ["build.ninja", "ninja"],
    "bazel": ["build.bazel", "workspace", "WORKSPACE"],
    "buck": ["BUCK"],
    "scons": ["sconstruct", "sconscript"],
    "qmake": [".pro"],
    "premake": ["premake4.lua", "premake5.lua"],
    "xcodebuild": ["project.pbxproj"],
    "cargo": ["cargo.toml"],
    "gradle": ["build.gradle", "build.gradle.kts"],
    "maven": ["pom.xml"],
    "python": ["setup.py", "pyproject.toml", "requirements.txt"],
}

# Priority order for build systems (more common/reliable first).
_PRIORITY_ORDER: list[str] = [
    "cmake",
    "make",
    "scons",
    "meson",
    "ninja",
    "bootstrap",
    "autoconf",
    "sln",
    "bazel",
    "buck",
    "qmake",
    "premake",
    "xcodebuild",
    "cargo",
    "gradle",
    "maven",
    "python",
]


def get_build_system(files: Iterable[str]) -> str:
    """Return the highest-priority build system detected in ``files``."""
    files = list(files)
    detected_tools: dict[str, list[str]] = {}

    for fname in files:
        fname_lower = fname.strip().lower()
        for build_tool, file_keywords in _BUILD_SYSTEMS.items():
            for file_keyword in file_keywords:
                if file_keyword in fname_lower:
                    detected_tools.setdefault(build_tool, []).append(fname)

    if not detected_tools:
        # Fall back to source-file patterns when no build file is present.
        has_cpp = any(
            any(f.endswith(ext) for ext in [".cpp", ".cc", ".cxx", ".c++"]) for f in files
        )
        has_c = any(f.endswith(".c") for f in files)
        has_python = any(f.endswith(".py") for f in files)

        if has_cpp or has_c:
            return "c_fallback"  # Can try direct compilation
        elif has_python:
            return "python"
        else:
            return "others"

    for tool in _PRIORITY_ORDER:
        if tool in detected_tools:
            return tool

    # Detected something outside the priority list: return the first one found.
    return next(iter(detected_tools))
