"""
Assemblage analyze tools
Chang 2022-01-03
"""

import logging

from elftools.elf.elffile import ELFFile


def get_build_system(files):
    """Analyze build tool from file list"""
    build_systems = {
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

    # Priority order for build systems (more common/reliable first)
    priority_order = [
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

    detected_tools = {}

    for fname in files:
        fname_lower = fname.strip().lower()
        for build_tool, file_keywords in build_systems.items():
            for file_keyword in file_keywords:
                if file_keyword in fname_lower:
                    if build_tool not in detected_tools:
                        detected_tools[build_tool] = []
                    detected_tools[build_tool].append(fname)

    if not detected_tools:
        # Check for common source code patterns to suggest fallback strategies
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

    # Return highest priority detected build system
    for tool in priority_order:
        if tool in detected_tools:
            return tool

    # If multiple detected but none in priority, return the first one found
    return list(detected_tools.keys())[0]


def extract_ELF_debug_line_info(binary_file):
    lines_offsets = {}
    with open(binary_file, "rb") as file:
        elffile = ELFFile(file)
        if not elffile.has_dwarf_info():
            logging.info("%s  file has no DWARF info", binary_file)
            return
        dwarfinfo = elffile.get_dwarf_info()
        for CU in dwarfinfo.iter_CUs():
            lines_program = []
            cu_die = CU.get_top_DIE()
            cu_name = cu_die.attributes["DW_AT_name"].value.decode()
            lines = dwarfinfo.line_program_for_CU(CU)
            debugsec_lines = lines.get_entries()
            for line in debugsec_lines:
                print(line)
                if line.state is not None:
                    lines_program.append((line.state.line, hex(line.state.address)))
            lines_offsets[cu_name] = lines_program
