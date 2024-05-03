"""
Assemblage analyze tools
Chang 2022-01-03
"""

import logging

from elftools.elf.elffile import ELFFile

def get_build_system(files):
    """Analyze build tool from file list"""
    build_systems = {"make": ["makefile"],
                     "cmake": ["cmakelists.txt"],
                     #  "travisci": [".travis.yml"],
                     #  "circleci": ["config.yml"],
                     #  "rake": ["rakefile"],
                     "sln": [".sln"],
                     "autoconf": ["configure"],
                     #  "java": ["build.gradle", "gradlew", "pom.xml"],
                     #  "ninja": ["ninja", "build.ninja"],
                     #  "bootstrap": ["bootstrap"]
                     }
    build_tools_list = []
    for fname in files:
        for build_tool, file_keywords in build_systems.items():
            for file_keyword in file_keywords:
                if file_keyword in fname.strip().lower():
                    build_tools_list.append(build_tool)
    build_tools = list(set(build_tools_list))
    if len(build_tools_list) == 0:
        return "others"
    else:
        return "/".join(build_tools)

def extract_ELF_debug_line_info(binary_file):
    lines_offsets = {}
    with open(binary_file, 'rb') as file:
        elffile = ELFFile(file)
        if not elffile.has_dwarf_info():
            logging.info('%s  file has no DWARF info', binary_file)
            return
        dwarfinfo = elffile.get_dwarf_info()
        for CU in dwarfinfo.iter_CUs():
            lines_program = []
            cu_die = CU.get_top_DIE()
            cu_name = cu_die.attributes['DW_AT_name'].value.decode()
            lines = dwarfinfo.line_program_for_CU(CU)
            debugsec_lines = lines.get_entries()
            for line in debugsec_lines:
                print(line)
                if line.state is not None:
                    lines_program.append((line.state.line,hex(line.state.address)))
            lines_offsets[cu_name] = lines_program
