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
import logging
from tqdm import tqdm
from git import Repo
from assemblage.worker.build_method import post_processing_pdb
from assemblage.consts import VCPKG_PATH, BUILD_FOLDER

logging.basicConfig()
logging.getLogger().setLevel(logging.DEBUG)

def clone_data(repo, optimization_level, mode):
	""" vcpkg don't need clone, pass the final result dir as clone dir """
	# vcpkg packge name is also stored in 'url' because of scraper code
	dest_path = f"{BUILD_FOLDER}/{repo}_{optimization_level}_{mode}"
	if os.path.exists(dest_path):
		shutil.rmtree(dest_path, ignore_errors=False, onerror=None)
	os.makedirs(os.path.join(dest_path, "triplets"))
	logging.info("Clone called")
	return b'No need for clone', 1, dest_path



def run_build(repo, target_dir, build_mode, library, optimization,
					slnfile, platform, compiler_version, version=None):
	""""""
	logging.info(f" >>> Building {repo} ...")
	triplet_cpu_arch = "x64"
	triplet_path = os.path.relpath(os.path.join(target_dir, "triplets", f"{triplet_cpu_arch}-{optimization.lower()}-windows.cmake"))
	triplet_flags = {"VCPKG_TARGET_ARCHITECTURE": triplet_cpu_arch, "VCPKG_CRT_LINKAGE":"dynamic", "VCPKG_LIBRARY_LINKAGE":"dynamic"}
	if build_mode.lower()=="release":
		triplet_flags["VCPKG_BUILD_TYPE"] = "release"
	else:
		triplet_flags["VCPKG_BUILD_TYPE"] = "debug"
	triplet_flags["CMAKE_CXX_FLAGS"] = f"/{optimization}"
	triplet_flags["CMAKE_C_FLAGS"] = f"/{optimization}"
	triplet_flags["VCPKG_CXX_FLAGS"] = f"/{optimization}"
	triplet_flags["VCPKG_C_FLAGS"] = f"/{optimization}"
	triplet_flags["CMAKE_CXX_FLAGS_RELEASE"] = f"/{optimization}"
	triplet_flags["CMAKE_C_FLAGS_RELEASE"] = f"/{optimization}"
	triplet_flags["VCPKG_CXX_FLAGS_RELEASE"] = f"/{optimization}"
	triplet_flags["VCPKG_C_FLAGS_RELEASE"] = f"/{optimization}"
	
	builddir = os.path.join(target_dir, "build"+os.urandom(8).hex())
	bindir = os.path.join(target_dir, "bin"+os.urandom(8).hex())
	with open(triplet_path, "w") as f:
		for x in triplet_flags:
			f.write(f"set({x} {triplet_flags[x]})\n")
	
	cmd = f"vcpkg install {repo} --overlay-triplets={target_dir}/triplets --x-install-root={builddir} --triplet {triplet_cpu_arch}-{optimization}-windows --x-packages-root={bindir}"
	logging.info(cmd)

	if not os.path.exists(f"Binaries/{repo}-{triplet_cpu_arch}-{optimization}-{version.replace(':','')}({compiler_version})"):
		try:
			os.system(cmd)
		except:
			pass
	else:
		logging.info("Alredy built")
	post_processing_pdb(
		bindir, build_mode, library=library, repoinfo={"url":repo, "updated_at": version}, toolset=compiler_version,
				optimization=optimization, source_codedir=target_dir, commit=version, movedir=f"Binaries/{repo}-{triplet_cpu_arch}-{optimization}-{version.replace(':','')}({compiler_version})")
	shutil.rmtree(r"C:\vcpkg\buildtrees", ignore_errors=True)
	shutil.rmtree(r"C:\vcpkg\packages", ignore_errors=True)
	shutil.rmtree(r"C:\vcpkg\downloads", ignore_errors=True)
	shutil.rmtree(BUILD_FOLDER, ignore_errors=True)

	return

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




def clone_data(repo, optimization_level, mode):
	""" vcpkg don't need clone, pass the final result dir as clone dir """
	# vcpkg packge name is also stored in 'url' because of scraper code
	dest_path = f"{BUILD_FOLDER}/{repo}_{optimization_level}_{mode}"
	if os.path.exists(dest_path):
		shutil.rmtree(dest_path, ignore_errors=False, onerror=None)
	os.makedirs(os.path.join(dest_path, "triplets"))
	logging.info("Clone called")
	return b'No need for clone', 1, dest_path



def run_build(repo, target_dir, build_mode, triplet_cpu_arch, optimization,
					slnfile, platform, compiler_version, version=None):
	""""""
	logging.info(f"Building {repo} ...")
	triplet_path = os.path.relpath(os.path.join(target_dir, "triplets", f"{triplet_cpu_arch}-{optimization.lower()}-windows.cmake"))
	triplet_flags = {"VCPKG_TARGET_ARCHITECTURE": triplet_cpu_arch, "VCPKG_CRT_LINKAGE":"dynamic", "VCPKG_LIBRARY_LINKAGE":"dynamic"}
	if build_mode.lower()=="release":
		triplet_flags["VCPKG_BUILD_TYPE"] = "release"
	else:
		triplet_flags["VCPKG_BUILD_TYPE"] = "debug"
	triplet_flags["CMAKE_CXX_FLAGS"] = f"/{optimization}"
	triplet_flags["CMAKE_C_FLAGS"] = f"/{optimization}"
	triplet_flags["VCPKG_CXX_FLAGS"] = f"/{optimization}"
	triplet_flags["VCPKG_C_FLAGS"] = f"/{optimization}"
	triplet_flags["CMAKE_CXX_FLAGS_RELEASE"] = f"/{optimization}"
	triplet_flags["CMAKE_C_FLAGS_RELEASE"] = f"/{optimization}"
	triplet_flags["VCPKG_CXX_FLAGS_RELEASE"] = f"/{optimization}"
	triplet_flags["VCPKG_C_FLAGS_RELEASE"] = f"/{optimization}"
	
	builddir = os.path.join(target_dir, "build"+os.urandom(8).hex())
	bindir = os.path.join(target_dir, "bin"+os.urandom(8).hex())
	with open(triplet_path, "w") as f:
		for x in triplet_flags:
			f.write(f"set({x} {triplet_flags[x]})\n")
	
	cmd = f"vcpkg install {repo} --overlay-triplets={target_dir}/triplets --x-install-root={builddir} --triplet {triplet_cpu_arch}-{optimization}-windows --x-packages-root={bindir}"
	logging.info(cmd)

	if not os.path.exists(f"Binaries/{repo}-{triplet_cpu_arch}-{optimization}-{version.replace(':','')}({compiler_version})"):
		try:
			os.system(cmd)
		except:
			pass
	else:
		logging.info("Alredy built")
	post_processing_pdb(
		bindir, build_mode, library=triplet_cpu_arch, repoinfo={"url":repo, "updated_at": version}, toolset=compiler_version,
				optimization=optimization, source_codedir=target_dir, commit=version, movedir=f"Binaries/{repo}-{triplet_cpu_arch}-{optimization}-{version.replace(':','')}({compiler_version})")
	shutil.rmtree(rf"{VCPKG_PATH}\buildtrees", ignore_errors=True)
	shutil.rmtree(rf"{VCPKG_PATH}\packages", ignore_errors=True)
	shutil.rmtree(rf"{VCPKG_PATH}\downloads", ignore_errors=True)
	shutil.rmtree(BUILD_FOLDER, ignore_errors=True)

	return 0

def vcpkg_git_treverse(vcpkgpath, packagename, version):
    packagename = packagename.lower()
    repo = Repo(vcpkgpath)
    print("Repo loaded")
    if packagename not in os.listdir(os.path.join(vcpkgpath, "ports")):
        return 1
    commit_hash = []

    for x in repo.iter_commits():
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
        print(l,r,mid, commit_hash[mid])
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
        print(x, packagename, version, cur_version)

        if cur_version in version:
            return 0
        for i in range(min(len(versions), len(versions_cur))):
            if versions[i] == versions_cur[i]:
                if i == len(versions)-1:
                    os.system(rf"cd {VCPKG_PATH}&&bootstrap-vcpkg.bat")
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
        print(l,r,mid, commit_hash[mid])
        if version in cur_version:
            return 0
    return 1