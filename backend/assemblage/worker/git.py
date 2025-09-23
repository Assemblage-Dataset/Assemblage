import datetime
import logging
import os
import shutil
import json
import time
import random
import string
import glob
import requests
import ntpath

from assemblage.worker.build_method_new import post_processing_pdb
from git import Repo
from tqdm import tqdm

BUILD_FOLDER = "Builds"

import logging
logging.basicConfig()
logging.getLogger().setLevel(logging.DEBUG)

def vcpkg_git_treverse(vcpkgpath, packagename, version):
    packagename = packagename.lower()
    repo = Repo(vcpkgpath)
    repo.git.checkout("c247b81088dbe46c159be6157944d5db8bf76b4a")
    if packagename not in os.listdir(os.path.join(vcpkgpath, "ports")):
        return 1
    commit_hash = []

    for x in tqdm(repo.iter_commits()):
        commit_hash.append([x.hexsha, x.committed_datetime])
    commit_hash.sort(key=lambda x:x[1])
    commit_hash = [x[0] for x in commit_hash]
    l = 0
    r = len(commit_hash)-1
    prev_commit_checked = ""
    while l!=r:
        mid = (l+r-1)//2
        if mid >= len(commit_hash):
            return 1
        x = commit_hash[mid]
        if prev_commit_checked == x:
            break
        repo.git.checkout(x, force=True)
        prev_commit_checked = x
        if os.path.isfile(os.path.join(vcpkgpath,"ports", packagename, "vcpkg.json")):
            repodata = json.load(open(os.path.join(vcpkgpath,"ports", packagename, "vcpkg.json")))
        elif os.path.isfile(os.path.join(vcpkgpath,"ports", packagename, "CONTROL")):
            control_contents = open(os.path.join(vcpkgpath,"ports", packagename, "CONTROL"), "r").readlines()
            for control_line in control_contents:
                if "Version:" in control_line:
                    repodata = {'version' : control_line.replace("Version:", "").strip()}
        else:
            continue
        cur_version = ""
        if 'version' in repodata:
            cur_version = repodata['version']
        if "version-string" in repodata:
            cur_version = repodata['version-string']
        version = version.replace("-", ".")
        cur_version = cur_version.replace("-", ".")
        versions = version.split(".")
        versions_cur = cur_version.split(".")

        if "." in version and "." not in cur_version:
            version = version.replace(".", "")
        if "." in cur_version and "." not in version:
            cur_version = cur_version.replace(".", "")
        logging.info("Package %s, package %s, looking for %s, current at %s", packagename, x, version, cur_version)

        if cur_version in version:
            return 0
        for i in range(min(len(versions), len(versions_cur))):
            if versions[i] == versions_cur[i]:
                if i == len(versions)-1:
                    os.system(rf"cd {vcpkgpath}&&bootstrap-vcpkg.bat")
                    os.system(r"vcpkg integrate install")
                    os.system(r"vcpkg update")
                    return 0
                continue
            if versions[i].isdigit() and versions_cur[i].isdigit()\
                and int(versions[i]) > int(versions_cur[i]):
                l = mid
                break
            else:
                r = mid
                break
        if version in cur_version:
            os.system(rf"cd {vcpkgpath}&&bootstrap-vcpkg.bat")
            os.system(r"vcpkg integrate install")
            os.system(r"vcpkg update")
            return 0
    return 1


def git_traverse_to_tag(repopath, tag):
    assert os.path.isdir(repopath)
    repo = Repo(repopath)
    repo.git.checkout(tag)
    del repo # Sometimes repo is not freed properly, not sure why