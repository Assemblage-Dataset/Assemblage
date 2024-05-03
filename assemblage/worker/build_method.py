"""
Decide build method based on files in repo directory

Assemblage Windows Worker Build Methods
1. Modify XML file
2. Build with msbuild
Chang
Yihao
"""

from abc import abstractclassmethod
import os
import glob
import re
import logging
import subprocess
import shutil
import signal
import json
import ftplib
import yaml
import random
import string
import hashlib

import boto3
from botocore.exceptions import ClientError
import requests
from git import Repo

from assemblage.worker.profile import AWSProfile
from assemblage.consts import BuildStatus, PDBJSONNAME, BINPATH
from assemblage.windows.parsers.proj import Project
from assemblage.windows.parsers.sln import Solution
from assemblage.analyze.analyze import get_build_system

logging.basicConfig(level=logging.INFO)

def cmd_with_output(cmd, timelimit=60, platform='linux', cwd=''):
    """ The cmd execution function """
    if isinstance(cmd, list):
        cmd = " ".join(cmd)
    if not cwd:
        with subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            close_fds=True,
                            shell=True) as process:
            try:
                out, err = process.communicate(timeout=timelimit)
                exit_code = process.wait()
                process.kill()
                return out, err, exit_code
            except subprocess.TimeoutExpired:
                if platform == 'linux':
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                return b"subprocess.TimeoutExpired", b"subprocess.TimeoutExpired", 1
    else:
        with subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            close_fds=True,
                            shell=True,
                            cwd=cwd) as process:
            try:
                out, err = process.communicate(timeout=timelimit)
                exit_code = process.wait()
                process.kill()
                return out, err, exit_code
            except subprocess.TimeoutExpired:
                if platform == 'linux':
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                return b"subprocess.TimeoutExpired", b"subprocess.TimeoutExpired", 1

class BuildStartegy:

    @abstractclassmethod
    def clone_data(self, repo) -> tuple[bytes, int, str]:
        """
        callback function of how a repository is cloned to local
        TODO: add definition of repo here
        clone_dir: build process later will use data in this dir, please clone to this dir
        return :
        (msg, status_code, clone_dir) : (bytes, int, str) 
        check BuildStatus for status code
        """

    @abstractclassmethod    
    def run_build(self, repo, target_dir, build_mode, library, optimization, slnfile,
                  platform, compiler_version) -> tuple[bytes, bytes, int]:
        """ callback function to build command, return...."""

    
    @abstractclassmethod
    def pre_build(self, Platform,
                    Buildmode,
                    Target_dir,
                    Optimization,
                    _tmp_dir,
                    VC_Version,
                    Favorsizeorspeed="",
                    Inlinefunctionexpansion="",
                    Intrinsicfunctions="") -> tuple[bytes, int, str]:
        """
        pre processing hook
        return:
        (message, status_code, filename)
        """


    @abstractclassmethod
    def post_build_hook(self,
                        dest_binfolder, build_mode, library, repoinfo, toolset,
                        optimization, commit_hexsha):
        """ post process hook  """


class DefaultBuildStrategy(BuildStartegy):

    def get_clone_dir(self, repo):
        """
        Form a target directory with repo information
        """
        hashedurl = str(hash(repo['url'])).replace("-", "")
        hashedurl = hashedurl + \
            ''.join(random.choice(string.ascii_lowercase) for _ in range(10))
        return f"{self.tmp_dir}/{hashedurl}"

    def clone_data(self, repo):
        """ Clone repo """
        logging.info("Cloning %s", repo["url"])
        clone_dir = self.get_clone_dir(repo)
        zip_url = repo["url"]+"/archive/refs/heads/master.zip"
        if self.platform == "linux":
            tmp_filename = os.urandom(16).hex()
            out, err, exit_code0 = cmd_with_output(
                f"wget -O {tmp_filename} {zip_url}", 60, self.platform)
            out, err, exit_code1 = cmd_with_output(
                f"unzip {tmp_filename} -d {clone_dir}", 60, self.platform)
            cloned_folder = ""
            try:
                cloned_folder = os.listdir(clone_dir)[0]
            except:
                "Cloned folder not exist", BuildStatus.FAILED
            out, err, exit_code = cmd_with_output(
                f"cd {clone_dir}/{cloned_folder}&&mv * ../", 60, self.platform)
            if exit_code == 0:
                return b'CLONE SUCCESS', BuildStatus.SUCCESS, clone_dir
            else:
                logging.error("Clone failed with output: \n %s \n error: \n %s", out, err)
        if self.platform == "windows":
            if "mod_timestamp" not in repo:
                try:
                    logging.info("Downloading zip %s", zip_url)
                    response = requests.get(zip_url)
                    tmp_file_path = os.path.join(self.tmp_dir, hashlib.md5(repo["url"].encode()).hexdigest(
                            )+''.join(random.choice(string.ascii_lowercase) for _ in range(5))+".zip")
                    open(tmp_file_path, "wb").write(response.content)
                    shutil.unpack_archive(tmp_file_path, clone_dir)
                    os.remove(tmp_file_path)
                    return b'CLONE SUCCESS', BuildStatus.SUCCESS, clone_dir
                except Exception as err:
                    logging.info(err)
            else:
                out, err, exit_code = cmd_with_output([
                    'gh', 'repo', 'clone', repo['url'], clone_dir, "--", "--depth",
                    "1", "--recursive"
                ], 60, self.platform)

                if exit_code == 10:
                    return err, BuildStatus.TIMEOUT, clone_dir

                elif exit_code == 0:
                        try:
                            repo = Repo(clone_dir)
                            for commit in list(repo.iter_commits()):
                                if commit.committed_date<repo["mod_timestamp"]:
                                    repo.git.checkout(commit.id)
                                    break
                            return b'CLONE SUCCESS', BuildStatus.SUCCESS, clone_dir
                        except Exception as err:
                            logging.info(err)
                            return (str(err)).encode(), BuildStatus.FAILED, clone_dir
                else:
                    return out + err, BuildStatus.FAILED, clone_dir

    def run_build(self,
                repo,
                target_dir,
                build_mode,
                library,
                optimization,
                slnfile=None,
                platform='linux',
                compiler_version='v142'):
        """ Generate cmd to execute """
        if platform.lower() == 'windows':
            cmd = ["powershell", "-Command", "msbuild"]
            if build_mode in ["Release", "Debug"]:
                cmd.append(f"/property:Configuration={build_mode}")
            if library == "x86" or library == "x86":
                cmd.append("/property:Platform=x86")
            elif library == "x64":
                cmd.append("/property:Platform=x64")
            elif library == "Mixed Platforms":
                cmd.append("/property:Platform='Mixed Platforms'")
            elif library == "Any CPU":
                cmd.append("/p:Platform=Any CPU")
            # cmd.append(f"/p:PlatformToolset={compiler_version}")
            if compiler_version in ["v140", "v141"]:
                cmd.append("/p:WindowsTargetPlatformVersion= ")
            cmd.append("/maxcpucount:16")
            cmd.append("/property:PostBuildEvent= ")
            cmd.append("/property:OutDir=assemblage_outdir_bin/")
            cmd.append(f"'{slnfile}'")
            cmd = " ".join(cmd)
            logging.info("Windows cmd generated: %s", cmd)
            return cmd_with_output(cmd, 600, platform)
        if platform.lower() == 'linux':
            files = []
            for filename in glob.iglob(target_dir + '**/**', recursive=True):
                files.append(filename.split("/")[-1])
            logging.info("%s files in repo", len(files))
            build_tool = get_build_system(files)
            cmd = ""
            if 'bootstrap' in build_tool:
                cmd = f'cd {target_dir} && ./bootstrap && ' \
                    'bash ./configure && timeout -m 5000000 make -j4'
            elif 'configure' in build_tool:
                cmd = f'cd {target_dir} && bash ./configure && ' \
                    'timeout -m 5000000 -- make -j4'
            elif 'cmake' in build_tool:
                cmd = f'cd {target_dir} && cmake -Bbuild ./ && cd build && ' \
                    'timeout -m 5000000 -- make -j4'
            elif 'make' in build_tool:
                cmd = f'cd {target_dir} && timeout -m 5000000 -- make -j4'
            logging.info("Linux cmd generated: %s", cmd)
            return cmd_with_output(cmd, 600, platform)

