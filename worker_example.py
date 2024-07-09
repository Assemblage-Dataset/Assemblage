import datetime
import logging
import os
import shutil
import json
import time
import random
import string
from tqdm import tqdm
import random
import glob
import requests
import ntpath

from assemblage.worker import build_method
from assemblage.worker.find_bin import find_elf_bin
from assemblage.worker.profile import AWSProfile
from assemblage.worker.build_method import DefaultBuildStrategy
from assemblage.consts import VCPKG_PATH
from assemblage.worker.vcpkg_functions import *

# Configs for the build
mode = "release"
compiler_version = 'v143'
library = "x64"


# Projects to be built
projects = {
     "openssl": ["1.1.1g", "1.1.1h"],
}

# Actual run
for project, versions in projects.items():
    project = project.lower()
    for version in versions:
        version = version.lower().split(" ")[0]
        result = vcpkg_git_treverse(VCPKG_PATH, project, version)
        if result!=0:
            continue
        for optimization_level in ["Od", "O1", "O2", "O3"]:
                msg, code, clonedir = clone_data(project, optimization_level, mode)
                run_build(project, clonedir, mode, library, optimization_level,
                    "", "Win-x86_64", compiler_version, version)
