import datetime
import logging
import os
import shutil
import json
import time
import random
import string
import random

from git import Repo
import os
import json

import glob
# import grpc
import requests
import ntpath

from tqdm import tqdm
import json

from assemblage.worker import build_method
from assemblage.worker.find_bin import find_elf_bin
from assemblage.worker.profile import AWSProfile
from assemblage.worker.build_method import DefaultBuildStrategy
from assemblage.consts import VCPKG_PATH
from assemblage.worker.vcpkg_functions import *
# Configs
BUILD_MODE = "release"
COMPILER_VERSION = 'v141'
ARCH = "x64"

        
with open("projects.json") as f:
    projects = json.load(f)


for project, versions in projects.items():
    project = project.lower()
    for version in versions:
        version = version.lower().split(" ")[0]
        result = vcpkg_git_treverse(VCPKG_PATH, project, version)
        if result!=0:
            continue
        for optimization_level in ["Od", "O1", "O2", "O3"]:
            msg, code, clonedir = clone_data(project, optimization_level, BUILD_MODE)
            run_build(project, clonedir, BUILD_MODE, ARCH, optimization_level,
                "", "Win-x86_64", COMPILER_VERSION, version)

from assemblage.worker.customize_strategies.zlib import *

obj = Zlib(clonedir=r"sources\zlib",
    collect_dir=r"Binaries",
    project_git_url="https://github.com/madler/zlib.git",
    optimization="",
    build_mode="release",
    arch="x64",
    tags=["1.0.1", ".2.0", "1.3.0"],
    compiler_version="Visual Studio 15 2017")

obj.run()