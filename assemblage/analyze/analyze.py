"""
Assemblage analyze tools
Chang 2022-01-03
"""


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
