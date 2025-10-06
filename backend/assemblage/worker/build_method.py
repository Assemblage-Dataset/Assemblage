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
from tempfile import tempdir
import time
import yaml
import random
import string
import hashlib
import boto3
import requests

from botocore.exceptions import ClientError
from git import Repo

from assemblage.worker.profile import AWSProfile
from assemblage.consts import BuildStatus, PDBJSONNAME, BINPATH, CloneStatus
from assemblage.windows.parsers.proj import Project
from assemblage.windows.parsers.sln import Solution
from assemblage.analyze.analyze import get_build_system
from assemblage.worker.ctags_parser import get_functions as ctags_get_functions
from assemblage.worker.clang_parser import get_functions as clang_get_functions
from typing import Tuple
from assemblage.config import Settings
logger = logging.getLogger(__name__)


settings = Settings()

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

def clean(folders):
    """ Clean the folders, may not be empty """
    for folder in folders:
        if os.path.exists(folder):
            shutil.rmtree(folder, ignore_errors=False, onerror=None)
            

class BuildStrategy:

    @abstractclassmethod
    def clone_data(self, repo) -> Tuple[bytes, int, str]:
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
                  platform, compiler_version) -> Tuple[bytes, bytes, int]:
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
                    Intrinsicfunctions="") -> Tuple[bytes, int, str]:
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
        logger.info("post build hooke")


class DefaultBuildStrategy(BuildStrategy):
    
    def __init__(self, tmp_dir = "/tmp", num_p_job=16):
        self.tmp_dir = tmp_dir
        self.num_p_job = num_p_job


    def clone_data(self, repo):
        """ Clone repo """
        clonedir = f"/tmp/{os.urandom(8).hex()}"
        out, err, exit_code = cmd_with_output(f'git clone --recursive {repo["url"]} {clonedir}', 600, "linux")
        
      
        return_code = CloneStatus.SUCCESS if exit_code == 0 else CloneStatus.FAILED # maybe try add more verboese errors?
        if return_code == CloneStatus.FAILED:
            logger.warning(f"Error in cloning data err={err}")
      
        return out, return_code, clonedir

    def run_build(self,
                repo,
                target_dir,
                build_mode,
                library,
                optimization,
                slnfile=None,
                platform='linux',
                compiler_version='v142') :
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
            logger.info("Windows cmd generated: %s", cmd)
                    
        if platform.lower() == 'linux':
            # this currently isnt being reached
            files = []
            for filename in glob.iglob(target_dir + '**/**', recursive=True):
                files.append(filename.split("/")[-1])
            logger.info("%s files in repo: %s", len(files), repo)
            logger.info(f"Files found in {target_dir} {os.listdir(target_dir)}") 

            
            build_tool = get_build_system(files)
            cmd = ""
            
            if settings.SAVE_ASSEMBLY:
                extra_flags = 'CFLAGS="$CFLAGS -save-temps=obj" CXXFLAGS="$CXXFLAGS -save-temps=obj"'
            else:
                extra_flags = 'CFLAGS="$CFLAGS" CXXFLAGS="$CXXFLAGS"'
        
                
            
            if 'bootstrap' in build_tool:
                cmd = f'cd {target_dir} && ./bootstrap && ' \
                    f'bash ./configure && timeout 10m make {extra_flags} -j{self.num_p_job}'
            elif 'configure' in build_tool:
                cmd = f'cd {target_dir} && bash ./configure && ' \
                    f'timeout 10m make {extra_flags} -j{self.num_p_job}'
            elif 'cmake' in build_tool:
                cmd = f'cd {target_dir} && cmake -Bbuild ./ && cd build && ' \
                    f'timeout 10m  make {extra_flags} -j{self.num_p_job}'
            elif 'make' in build_tool:
                cmd = f'cd {target_dir} && timeout 10m make {extra_flags} -j{self.num_p_job}'
            logger.info("Linux cmd generated: %s", cmd)
            
            if cmd == "":
                logger.warning("No build command created for linux")
                return "No Build Command Made", BuildStatus.FAILED
            
            cmd += "&& ls -a"
            
        
        out, err, exit_code = cmd_with_output(cmd, 600, platform)
        return_code = BuildStatus.SUCCESS if exit_code == 0 else BuildStatus.FAILED

        if return_code is BuildStatus.SUCCESS:
            logger.info(f"Output decode on success: {out.decode()}")
        return out.decode() + err.decode(), return_code






class WindowsDefaultStrategy(DefaultBuildStrategy):

    def dia_list_binaries(self, dest_binfolder):
        """ get binary file under the binfolder """
        bfiles = []
        for single_file in glob.glob(dest_binfolder + '/**/*', recursive=True):
            if os.path.isfile(single_file) and (single_file.lower().endswith("pdb") or single_file.lower().endswith("exe") or single_file.lower().endswith("dll") or single_file.lower().endswith("lib")):
                bfiles.append(single_file)
        return bfiles

    def pre_build(self, Platform,
                        Buildmode,
                        Target_dir,
                        Optimization,
                        _tmp_dir,
                        VC_Version,
                        Favorsizeorspeed="",
                        Inlinefunctionexpansion="",
                        Intrinsicfunctions=""):
        """ Modifying the build file to save flags """
        files = []
        for filename in glob.iglob(Target_dir + '**/**', recursive=True):
            files.append(filename)
        slnfile = ""
        projfiles = []
        for f in files:
            if f.endswith("sln"):
                slnfile = f
            if f.endswith("vcxproj"):
                projfiles.append(f)
        if slnfile == "":
            logger.error("No solution file found")
            return "No SLN file found", BuildStatus.SUCCESS, ""
        try:
            sln = Solution(slnfile)
            sln.set_config(Platform, Buildmode)
        except:
            logger.info("SLN parsing err, but continue with vcxproj files")
        try:
            for projfile in projfiles:
                projobj = Project(projfile)
                projobj.set_toolset_version(VC_Version)
                projobj.set_optimization(Optimization)
                if Favorsizeorspeed != "":
                    projobj.set_favorsizeorspeed(Favorsizeorspeed)
                if Inlinefunctionexpansion != "":
                    projobj.set_inlinefunctionexpansion(Inlinefunctionexpansion)
                if Intrinsicfunctions != "":
                    projobj.enable_intrinsicfunctions()
                projobj.write()
                projobj_saved = Project(projfile)
                optimization_mode = ""
                if "O2" in Optimization:
                    optimization_mode = "MaxSpeed"
                elif "O1" in Optimization:
                    optimization_mode = "MinSpace"
                elif "Ox" in Optimization:
                    optimization_mode = "Full"
                else:
                    optimization_mode = "Disabled"
                logger.info("Read config: %s, correct: %s",
                            projobj_saved.get_optimization(), optimization_mode)
                assert optimization_mode == projobj_saved.get_optimization()
        except FileNotFoundError:
            logger.error("Build File not exist")
            return "Parsing FileNotFoundError", BuildStatus.FAILED, ""
        except AttributeError as err:
            logger.error("Build vcxproj file parsing error %s", str(err))
            return "Parsing AttributeError", BuildStatus.FAILED, ""
        except KeyError:
            logger.error("Build vcxproj file setting error")
            return "Parsing file key error", BuildStatus.FAILED, ""
        except AssertionError:
            return "Parsing file verification error", BuildStatus.FAILED, ""
        logger.info("Parsing success")
        return "Parsing success", BuildStatus.SUCCESS, slnfile

    def dia_get_func_funcinfo(self, binfile, source_code_prefix):
        """ Process the bin to get the info and function"""
        file_cache = {}
        if source_code_prefix:
            for f in glob.glob(source_code_prefix + '/**/*', recursive=True):
                if os.path.isfile(f) and ".git" not in f and len(os.path.basename(f))>3:
                    try:
                        with open(f, 'r', encoding="utf-8") as source_f:
                            assert os.path.basename(f).lower() not in file_cache.keys()
                            file_cache[f] = source_f.readlines()
                    except Exception as e:
                        try:
                            with open(f, 'r', encoding="utf-16") as source_f:
                                assert os.path.basename(f).lower() not in file_cache.keys()
                                file_cache[f] = source_f.readlines()
                        except Exception as e:
                            pass

        if len(file_cache.keys())<1:
            return {}, {}, ""

        # binfile = binfile.replace("/", "\\")
        binfolder = os.path.dirname(binfile)
        binfile = binfile.split("\\")[-1]
        logger.info("Processing %s, move to %s", binfile, binfolder)
        cmd = f"Dia2Dump -lines * {binfile}"
        out, _err, _exit_code = cmd_with_output(cmd, platform='windows', cwd=binfolder)
        file_cache = {}
        try:
            lines_notclean = out.decode().split("\r\n")
        except:
            logger.info("Dia2dump error")
            lines_notclean = []
        lines = []
        for line in lines_notclean:
            lines.append(line.strip())

        lines = []
        for line in lines_notclean:
            lines.append(line.strip())
        funcs_infos = {}
        rva_seg_length = 0
        dbg_seg_length = 0
        source_file = ""
        lines_infos = {}
        file_hash_lookup = {}
        for i, line in enumerate(lines):
            lines_dict = {}
            if line.startswith("**"):
                func_name = line.replace("**", "").replace(" ", "").strip()
                rva_seg_length = 0
                dbg_seg_length = 0
                func_name_infoitem = {}
            if line.startswith("line"):
                if len(re.split(r"\w:\\", line)) == 2:
                    source_file = re.findall(r"\w:\\", line)[0] + re.split(r"\w:\\", line)[1]
                    if "MD5" in source_file:
                        source_file_cleaned = source_file.split(" (MD5: ")[0]
                        source_file_md5 = source_file.split(" (MD5: ")[1].replace(")", "")
                        file_hash_lookup[source_file_cleaned.strip()]=source_file_md5
                    if "0x3" in source_file:
                        source_file_cleaned = source_file.split(" (0x3: ")[0]
                        source_file_md5 = source_file.split(" (0x3: ")[1].replace(")", "")
                        file_hash_lookup[source_file_cleaned.strip()]=source_file_md5
                rva = re.findall(r"at \[\w+\]", line)[0].replace("at ", "").replace("[", "").replace("]", "")
                length = int(re.findall(r"len \= \w+", line)[0].replace("len = ", ""), 16)
                line_number = int(re.findall(r"line \d+", line)[0].replace("line ", ""))
                lines_dict["line_number"] = line_number
                lines_dict["rva"] = rva
                lines_dict["length"] = length
                lines_dict["source_code"] = ""
                if source_file_cleaned not in file_cache.keys():
                    try:
                        file_cache[source_file_cleaned] = open(source_file_cleaned, 'r', encoding="utf-8", errors="ignore").readlines()
                    except:
                        file_cache[source_file_cleaned] = [""]
                filecontent = file_cache[source_file_cleaned]
                if len(filecontent)>line_number-1:
                    lines_dict["source_code"] = filecontent[line_number-1].strip()
                
                lines_dict["source_file"] = source_file

                if "rva_start" not in func_name_infoitem.keys():
                    func_name_infoitem["rva_start"] = rva
                if line_number > 10000000:
                    dbg_seg_length = dbg_seg_length + length
                rva_seg_length = rva_seg_length + length
                if i+1<len(lines) and (not lines[i + 1].startswith("line")):
                    func_name_infoitem["rva_end"] = str(
                        hex(int(rva, 16) + int(length))).replace("0x", "").rjust(
                            len(rva), "0")
                    if func_name in funcs_infos.keys():
                        funcs_infos[func_name].append(func_name_infoitem)
                    else:
                        funcs_infos[func_name] = [func_name_infoitem]
                if func_name in lines_infos.keys():
                    lines_infos[func_name].append(lines_dict)
                else:
                    lines_infos[func_name] = [lines_dict]
        return funcs_infos, lines_infos, source_file


    def post_build_hook(self, dest_binfolder, build_mode, library, repoinfo, toolset,
                        optimization, commit_hexsha):
        """ Postprocess the pdb """
        bin_files = self.dia_list_binaries(dest_binfolder)
        outer_list = []
        func_cache = {}
        if not os.path.isdir(movedir):
            os.makedirs(movedir)
        for _, binfile in enumerate(bin_files):
            logger.info("Moving %s -> %s", binfile, os.path.join(movedir, os.path.basename(binfile)))
            shutil.copy(binfile, os.path.join(movedir, os.path.basename(binfile)))

            funcs_infos, lines_infos, source_file = self.dia_get_func_funcinfo(binfile, source_codedir)
            item_dict = {}
            item_dict["functions"] = []
            item_dict["file"] = binfile
            for func_name, infos in funcs_infos.items():
                functions_val = {}
                functions_val["function_name"] = func_name
                functions_val["source_file"] = source_file
                functions_val["function_info"] = funcs_infos[func_name]
                functions_val["lines"] = lines_infos[func_name]
                if len(functions_val["lines"])>0:
                    functions_val["source_file"] = functions_val["lines"][0]["source_file"]

                
                if "MD5" in functions_val["source_file"]:
                    source_file_cleaned = functions_val["source_file"].split(" (MD5: ")[0]
                elif " (0x3: " in functions_val["source_file"]:
                    source_file_cleaned = functions_val["source_file"].split(" (0x3: ")[0]
                else:
                    source_file_cleaned = functions_val["source_file"]

                # match priority clangfirst
                if source_file_cleaned not in func_cache.keys():
                    try:
                        func_cache[source_file_cleaned] = clang_get_functions(source_file_cleaned)
                    except Exception as e:
                        logger.info("Clang parser error %s", e)
                        func_cache[source_file_cleaned] = []
                    beforetime = time.time()
                    func_cache[source_file_cleaned] += ctags_get_functions(source_file_cleaned)
                
                funcsourceinfo = func_cache[source_file_cleaned]
                for func in funcsourceinfo:
                    if "::" in func_name and "::" in func[0]:
                        pass
                    elif "::" in func_name:
                        func_name = func_name.split("::")[-1]
                    elif "::" in func[0]:
                        func[0] = func[0].split("::")[-1]
                    if func[0].lower() == func_name.lower():
                        functions_val["ctag_definitions"] = func[3]
                        functions_val["top_comments"] = func[4]
                        functions_val["body_comments"] = func[6]
                        functions_val["source_codes_ctags"] = func[5]
                        functions_val["prototype"] = func[7]
                        functions_val["source_codes"] = func[9]
                        for line_info_captured in functions_val["lines"]:
                            if (not line_info_captured["source_code"]) and (line_info_captured["line_number"] in func[8].keys()):
                                line_info_captured["source_code"] = func[8][line_info_captured["line_number"]]
                                break
                        break

                item_dict["functions"].append(functions_val)
            outer_list.append(item_dict)
        try:
            assemblage_meta = {}
            assemblage_meta["Platform"] = library
            assemblage_meta["Build_mode"] = build_mode
            assemblage_meta["Toolset_version"] = toolset
            assemblage_meta["URL"] = repoinfo["url"]
            assemblage_meta["Binary_info_list"] = outer_list
            assemblage_meta["Optimization"] = optimization
            assemblage_meta["Pushed_at"] = repoinfo["updated_at"]
            assemblage_meta["Commit"] = commit
            with open(os.path.join(dest_binfolder, PDBJSONNAME), "w") as outfile:
                json.dump(assemblage_meta, outfile, sort_keys=False, indent=4)
        except FileNotFoundError:
            logger.info("Pdbjsonfile not found")
        if not os.path.isdir(movedir):
            os.makedirs(movedir)
        shutil.move(os.path.join(dest_binfolder, PDBJSONNAME), movedir)



    def run_build(self,
            repo,
            target_dir,
            build_mode,
            library,
            optimization,
            slnfile=None,
            platform='linux',
            compiler_version='v142', 
            num_p_job=16):
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
            logger.info("Windows cmd generated: %s", cmd)
            return cmd_with_output(cmd, 600, platform)
        if platform.lower() == 'linux':
            files = []
            for filename in glob.iglob(target_dir + '**/**', recursive=True):
                files.append(filename.split("/")[-1])
            logger.info("%s files in repo", len(files))
            build_tool = get_build_system(files)
            if settings.SAVE_ASSEMBLY:
                cflags = 'CFLAGS="$CFLAGS -save-temps"'
                logger.info("Saving .s and .o files as well ")
            else:
                cflags = 'CLAGS="$CFLAGS"'
                
            cmd = ""
            if 'bootstrap' in build_tool:
                cmd = f'cd {target_dir} && ./bootstrap && ' \
                    f'bash ./configure && timeout -m 5000000 make {cflags} -j{self.num_p_job}'
            elif 'configure' in build_tool:
                cmd = f'cd {target_dir} && bash ./configure && ' \
                    f'timeout -m 5000000 -- make {cflags} -j{self.num_p_job}'
            elif 'cmake' in build_tool:
                cmd = f'cd {target_dir} && cmake -B build ./ && cd build && ' \
                    f'timeout -m 5000000 -- make {cflags} -j{self.num_p_job}'
            elif 'make' in build_tool:
                cmd = f'cd {target_dir} && timeout -m 5000000 -- make {cflags} -j{self.num_p_job}'
            logger.info("Linux cmd generated: %s", cmd)
            out, err, exit_code = cmd_with_output(cmd, 600, platform)
            return_code = BuildStatus.SUCCESS if exit_code == 0 else BuildStatus.FAILED
            if return_code == BuildStatus.SUCCESS:
                logger.warning(f"BUILD STATUS FOR {repo} succeeded!!!")
            return out.decode() + err.decode(), return_code


class vcpkgStrategy(DefaultBuildStrategy):


    def clone_data(repo, optimization_level, mode):
        """ vcpkg don't need clone, pass the final result dir as clone dir """
        # vcpkg packge name is also stored in 'url' because of scraper code
        dest_path = f"{BUILD_FOLDER}/{repo}_{optimization_level}_{mode}"
        if os.path.exists(dest_path):
            shutil.rmtree(dest_path, ignore_errors=False, onerror=None)
        os.makedirs(os.path.join(dest_path, "triplets"))
        logger.info("Clone called")
        return b'No need for clone', 0, dest_path

    def pre_build(repo, target_dir, build_mode, library, optimization,
                        slnfile, platform, compiler_version, version=None):
        return b'No need for precheck', 0, ""


    def run_build(repo, target_dir, build_mode, library, optimization,
                        slnfile, platform, compiler_version, version=None):
        """"""
        logger.info(f" >>> Building {repo} ...")
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

        if not os.path.exists(f"Binaries/{repo}-{triplet_cpu_arch}-{optimization}-{version.replace(':','')}({compiler_version})"):
            os.system(cmd)
            # Don't use subprocess, will cause weird error during running
        else:
            logger.info("Alredy built")
        post_processing_pdb(
            bindir, build_mode, library=library, repoinfo={"url":repo, "updated_at": version}, toolset=compiler_version,
                    optimization=optimization, source_codedir=target_dir, commit=version, movedir=f"Binaries/{repo}-{triplet_cpu_arch}-{optimization}-{version.replace(':','')}({compiler_version})")
        shutil.rmtree(r"C:\vcpkg\buildtrees", ignore_errors=True)
        shutil.rmtree(r"C:\vcpkg\packages", ignore_errors=True)
        shutil.rmtree(r"C:\vcpkg\downloads", ignore_errors=True)
        shutil.rmtree(BUILD_FOLDER, ignore_errors=True)

        return 
    