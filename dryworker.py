import datetime
import logging
import os
import shutil
import json
import time
import random
import string

import glob
# import grpc
import requests
import ntpath

from assemblage.worker import build_method
from assemblage.worker.find_bin import find_elf_bin
from assemblage.worker.profile import AWSProfile
from assemblage.worker.build_method import DefaultBuildStrategy

from vcpkg_functions import *

VCPKG_COMMIT = "5fa0f075ea51f305b627ecd5e050a363707353ff"
VCPKG_PATH = rf"C:\Users\yukim\OneDrive\Documents\vcpkg"

mode = "release"
compiler_version = 'v142'
library = "x86/x64"

def taskwrap(url, opt, version):
    msg, code, clonedir = clone_data(url, opt, mode)
    try:
        run_build(url, clonedir, mode, library, opt,
					"", "", compiler_version, version)
    except:
        pass


import json
projects = []
with open("db.json", "r") as f:
    projects = json.load(f)

for project in projects:
    for optimization_level in ["Od"]:
        repo = project
        version_byvcpkg = "?"
        if os.path.isfile(rf"{VCPKG_PATH}\ports\{repo}\vcpkg.json"):
            repodata = json.load(open(rf"{VCPKG_PATH}\ports\{repo}\vcpkg.json"))
        else:
            if os.path.isfile(rf"{VCPKG_PATH}\ports\{repo}\CONTROL"):
                fcontent = open(rf"{VCPKG_PATH}\ports\{repo}\CONTROL").readlines()
                for line in fcontent:
                    if "Version:" in line:
                        version_byvcpkg = line.strip()

        if 'version' in repodata:
            version_byvcpkg = (repodata['version'])
        if "version-semver" in repodata:
            version_byvcpkg = repodata['version-semver']
        if "version-string" in repodata:
            version_byvcpkg = repodata['version-string']

        taskwrap(project, optimization_level, version_byvcpkg)
    break