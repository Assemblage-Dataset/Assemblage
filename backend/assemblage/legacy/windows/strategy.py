"""Frozen Windows/MSVC build strategy (quarantined).

Moved verbatim out of the old ``worker/build_method.py`` during the P7 builder
re-architecture. Only the imports were adapted (``assemblage.consts`` shim,
``assemblage.legacy.windows.parsers``); the class bodies are unchanged.

This module carries its own copy of the pre-re-architecture *fat*
``BuildStrategy`` base (clone/restore/find helpers + ``cmd_with_output``) that
``WindowsDefaultStrategy`` depends on, so the quarantine is self-contained and
imports cleanly. It is **not** the slim :class:`assemblage.build.strategy.BuildStrategy`
ABC the Linux path uses.

Known signature drift (documented, not fixed — this code is frozen and only
reachable via the lazy Windows branch of ``make_strategy``): ``run_build`` /
``pre_build`` / ``post_build_hook`` here take a positional ``build_mode`` that the
re-architected Linux pipeline never passes; the MSVC path predates that pipeline
and is not exercised by any gate.
"""

import glob
import logging
import os
import re
import shutil
import signal
import subprocess
from abc import abstractmethod
from urllib.parse import urlparse

import pefile
from elftools.common.exceptions import ELFError
from elftools.elf.elffile import ELFFile

from assemblage.consts import BINPATH, BuildStatus, CloneStatus
from assemblage.legacy.windows.parsers.proj import Project
from assemblage.legacy.windows.parsers.sln import Solution

logger = logging.getLogger(__name__)


class BuildStrategy:
    def __init__(
        self,
        compiler: str,
        language: str,
        library: str,
        save_assembly: bool = False,
        base_path: str = BINPATH,
    ):
        self.save_assembly = save_assembly
        self.compiler: str = compiler
        self.language: str = language
        self.compiler_version = self._get_compiler_version()

        logger.debug(f"Compiler version: {self.compiler_version}")
        self.toolset_version = self._get_toolset_version()

        self.library = library
        self.platform: str
        # either BINPATH/ or TEMP/. defaults to BINPATH ( C:/binaries or /binaries)
        self.base_path: str = base_path

        logger.debug(f"Base path set to: {base_path}")
        self.mark_dir_as_safe(base_path)  # remove once other things fixed

    def cmd_with_output(self, cmd: str, timelimit=60, cwd=""):
        """
        Run a command and return stdout, stderr, and exit code.
        Ensures handles are closed properly on Windows to avoid file locks.
        """

        popen_kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "shell": True,
        }

        if cwd:
            popen_kwargs["cwd"] = cwd

        # Ensure child process does not inherit handles
        popen_kwargs["close_fds"] = True

        logger.debug(f"Starting process: {cmd}")

        with subprocess.Popen(cmd, **popen_kwargs) as process:
            try:
                out, err = process.communicate(timeout=timelimit)
                exit_code = process.returncode
                logger.debug(f"Command exited with code {exit_code}")
                return out, err, exit_code

            except subprocess.TimeoutExpired:
                logger.debug(f"Command timed out: {cmd}")
                try:
                    if self.platform != "windows":
                        os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                    else:
                        process.kill()
                except Exception as e:
                    logger.warning(f"Failed to kill process: {e}")
                return b"", b"subprocess.TimeoutExpired", 1
            except Exception as e:
                logger.warning(f"Something went wrong runnign cmd: {cmd} - {e}")
                return b"", b"{e}", 1

    def mark_dir_as_safe(self, path):
        cmd = f"git config --global --add safe.directory {path}"
        out, err, code = self.cmd_with_output(cmd, 600)
        if code != 0:
            logger.error(f"Failed to mark as safe, rest of commands may fail: {err}. {out}")

    def get_project_commit(self, clone_dir: str) -> str:
        """
        Temporary function. REMOVE once scrape gets commit
        """

        cmd = "git rev-parse --short=12 HEAD"
        out, err, code = self.cmd_with_output(cmd, 600, clone_dir)
        if code == 0:
            commit_hash = out.decode().strip()
        else:
            logger.error(f"Failed to get commit hash: {err.decode().strip()}")
            commit_hash = "Unknown"
        return commit_hash

    def clone_data(self, url) -> tuple[bytes | str | CloneStatus | CloneStatus]:
        """Clone repo
        If using s3 storage, then dont use temp, otherwise save to a temporary directory

        """

        user_name, project_name = self.parse_github_name(url)
        # no longer random + will now group projects from the same user together...

        if not user_name:
            user_name = os.urandom(8).hex()
        if not project_name:
            project_name = os.urandom(8).hex()

        git_user_dir = f"{self.base_path}/projects/{user_name}"

        clone_dir = f"{git_user_dir}/{project_name}"
        # ensure that user's directory exists
        os.makedirs(f"{git_user_dir}", exist_ok=True)
        cmd = ""
        cwd = ""

        # clone dir exists -- likely project already has been cloned
        if os.path.isdir(clone_dir):
            logger.debug(
                f"Target clone directory '{clone_dir}' already cloned: attempting to pull... "
            )
            cmd = "git pull --recurse-submodules"
            cwd = clone_dir
            # TODO: check for errors, more sophisticated git pull behavior?
        else:
            # first access of this project. cwd is set to "" so we can pass clone_dir as a destination
            cmd = f"git clone --recursive {url} {clone_dir}/"

        out, err, exit_code = self.cmd_with_output(cmd, 600, cwd=cwd)

        # # see above for how i feel about this
        self.own_dir(git_user_dir)  # ensure all projects
        # # maybe try add more verbose errors?
        return_code = CloneStatus.SUCCESS if exit_code == 0 else CloneStatus.FAILED
        if return_code == CloneStatus.FAILED:
            # clean up after a failed clone
            try:
                # will fail if not empty, ie the git user has a nother project already cloned
                os.removedirs(f"{git_user_dir}")
            except:
                pass
            try:
                os.removedirs(f"{clone_dir}")
            except:
                pass
            logger.warning(f"Error in cloning data err={err}")

        self.mark_dir_as_safe(clone_dir)

        return out, return_code, clone_dir

    def restore_from_archive(self, archive_path: str, url: str):
        """Restore a project from a downloaded S3 tarball instead of git-cloning.

        Returns the same (message, CloneStatus, clone_dir) tuple as clone_data().
        """
        user_name, project_name = self.parse_github_name(url)
        if not user_name:
            user_name = os.urandom(8).hex()
        if not project_name:
            project_name = os.urandom(8).hex()

        git_user_dir = f"{self.base_path}/projects/{user_name}"
        clone_dir = f"{git_user_dir}/{project_name}"
        os.makedirs(clone_dir, exist_ok=True)

        try:
            shutil.unpack_archive(archive_path, clone_dir)
            self.own_dir(git_user_dir)
            self.mark_dir_as_safe(clone_dir)
            logger.info(f"Restored {url} from S3 archive into {clone_dir}")
            return b"Restored from S3 archive", CloneStatus.SUCCESS, clone_dir
        except Exception as e:
            logger.warning(f"Failed to restore archive for {url}: {e}")
            try:
                shutil.rmtree(clone_dir, ignore_errors=True)
            except Exception:
                pass
            return str(e).encode(), CloneStatus.FAILED, clone_dir

    def parse_github_name(self, url):
        if url.endswith(".git"):
            url = url[:-4]

        if url.startswith("git@"):
            path = url.split(":", 1)[1]
        else:
            path = urlparse(url).path

        parts = path.strip("/").split("/")
        if len(parts) >= 2:
            return parts[0], parts[1]
        return None, None

    def find_binaries(self, path: str) -> set:
        """Find elf files and executables and other build artifacts to save"""
        logger.info(
            f"Finding executables in {path}, saving assembly files too: {self.save_assembly}"
        )
        file_paths = set()
        # Skip directories that contain pre-existing binaries (vendored code,
        # packaging artifacts, test fixtures) rather than binaries we built.
        _SKIP_DIRS = frozenset(
            {
                ".git",
                "third_party",
                "3rdparty",
                "thirdparty",
                "vendor",
                "external",
                "deps",
                "debian",
                "node_modules",
                ".cache",
                "testdata",
                "test_data",
            }
        )
        for root, dirs, file_names in os.walk(os.path.realpath(path)):
            dirs[:] = [d for d in dirs if d.lower() not in _SKIP_DIRS]

            for file_name in file_names:
                location = f"{root}/{file_name}"
                location_lc = location.lower()
                if not os.path.exists(location):
                    continue
                try:
                    binary_exts = (".pdb", ".exe", ".dll", ".lib")
                    assembly_exts = (".s", ".ii", ".bc", ".S", ".obj", ".asm", ".cod")
                    # optional assembly/ other artifacts
                    if self.save_assembly and location_lc.endswith(assembly_exts):
                        file_paths.add(location)
                        continue

                    if (
                        file_name == "pdbinfo.json"
                    ):  # not really an artifact, not sure where else to put this though
                        file_paths.add(location)
                        continue
                    # executables
                    if location_lc.endswith(binary_exts) and os.path.isfile(location):
                        file_paths.add(location)
                        continue
                    with open(location, "rb") as f:
                        if self.platform == "linux":
                            try:
                                ef = ELFFile(f)
                                if (
                                    ef.header["e_type"] == "ET_EXEC"
                                    or ef.header["e_type"] == "ET_DYN"
                                ):
                                    file_paths.add(location)
                            except ELFError:
                                continue
                        elif self.platform == "windows":
                            try:
                                pe = pefile.PE(location)
                                file_paths.add(location)
                            except pefile.PEFormatError:
                                continue

                except OSError:
                    continue

        logger.debug(f"Found executables: {file_paths}")
        return file_paths

    @abstractmethod
    def _get_compiler_version(self) -> str:
        pass

    @abstractmethod
    def _get_toolset_version(self) -> str | None:
        # only needed for windows
        pass

    @abstractmethod
    def own_dir(self, dir: str):
        """' A workaround function to fix ownership of the binaries directory. Owns a particular directory"""

    @abstractmethod
    def run_build(
        self, repo, clone_dir, compiler_flag="", slnfile=None, build_mode="RelWithDebInfo"
    ) -> tuple[bytes, bytes, int]:
        """callback function to build command, return...."""

    @abstractmethod
    def pre_build(
        self,
        clone_dir,
        compiler_flag: str = "",
        build_mode="RelWithDebInfo",
        favorsizeorspeed: None | str = None,
        inlinefunctionexpansion: None | str = None,
        intrinsicfunctions: bool = False,
    ):
        """
        pre processing hook
        return:
        (message, status_code, filename)
        """

    @abstractmethod
    def post_build_hook(
        self,
        dest_binfolder,
        repoinfo,
        compiler_flag="",
        commit_hexsha="",
        build_mode="RelWithDebInfo",
        original_files=None,
    ):
        """post process hook"""


class WindowsDefaultStrategy(BuildStrategy):
    # for visual studio code + msbuild complimations
    # compiler should be an enum of supported...
    def __init__(
        self,
        compiler: str,
        language: str,
        library: str,
        save_assembly: bool,
        num_p_job=16,
        base_path: str = BINPATH,
    ):
        super().__init__(
            compiler,
            language=language,
            save_assembly=save_assembly,
            library=library,
            base_path=base_path,
        )
        self.num_p_job = num_p_job
        self.platform = "windows"

    def _get_compiler_version(self) -> str | None:
        # currently this will only work for msvc. future can add more options
        # unfortunatley too, dont think this will work with env variables like get toolset version below
        try:
            _, err, code = self.cmd_with_output("cl.exe")

            match = re.search(r"Version ([\d.]+)", err.decode(errors="ignore"))

            if match:
                return match.group(1)

        except Exception as e:
            logger.warning(f"Failed to get compiler version: {e}")

        return None

    def _get_toolset_version(self):
        try:
            # Get the exact VC Tools version
            vc_tools_version = os.getenv("VCToolsVersion")

            if not vc_tools_version:
                logger.warning("VCToolsVersion environment variable not set")
                return
            logger.info(f"Detected VC toolset version: {vc_tools_version}")
            return vc_tools_version

        except Exception as e:
            logger.warning(f"Failed to get toolset version: {e}")

    def dia_list_binaries(self, dest_binfolder):
        """get binary file under the binfolder"""
        bfiles = []
        for single_file in glob.glob(dest_binfolder + "/**/*", recursive=True):
            if os.path.isfile(single_file) and (
                single_file.lower().endswith("pdb")
                or single_file.lower().endswith("exe")
                or single_file.lower().endswith("dll")
                or single_file.lower().endswith("lib")
            ):
                bfiles.append(single_file)
        return bfiles

    def pre_build(
        self,
        build_mode,
        clone_dir,
        compiler_flag: str = "",
        favorsizeorspeed: None | str = None,
        inlinefunctionexpansion: None | str = None,
        intrinsicfunctions: bool = False,
    ):
        """Modifying the build file to save flags"""
        files = []
        for filename in glob.iglob(clone_dir + "**/**", recursive=True):
            files.append(filename)
        slnfile = ""
        projfiles = []
        for f in files:
            if f.endswith(".sln"):
                slnfile = f
            if f.endswith("vcxproj"):
                projfiles.append(f)
        if slnfile == "":
            return None
        logger.debug(f"Creating solution now with slnfile: {slnfile}")
        try:
            sln = Solution(slnfile)
            sln.set_config("Windows", build_mode)
        except:
            logger.info("SLN parsing err, but continue with vcxproj files")

        logger.debug("Now analysing projfiles")
        try:
            for projfile in projfiles:
                projobj = Project(projfile)
                projobj.set_toolset_version(self.compiler_version)
                projobj.set_optimization(compiler_flag)
                if favorsizeorspeed:
                    projobj.set_favorsizeorspeed(favorsizeorspeed)
                if inlinefunctionexpansion:
                    projobj.set_inlinefunctionexpansion(inlinefunctionexpansion)
                if intrinsicfunctions:
                    projobj.enable_intrinsicfunctions()

                if self.save_assembly:
                    # this should save the assembly. maybe
                    projobj.save_assembly()

                projobj.write()
                projobj_saved = Project(projfile)

                logger.info(
                    "Read config: %s, flag: %s", projobj_saved.get_optimization(), compiler_flag
                )

                logger.debug(f"Finished processing projobj file: {projobj}")
        except FileNotFoundError:
            logger.error("Build File not exist")
            return None
        except AttributeError as err:
            logger.error("Build vcxproj file parsing error %s %s", str(err), projfile)

            return None
        except KeyError:
            logger.error("Build vcxproj file setting error")
            return None
        except AssertionError:
            return None
        logger.debug("Parsing success")
        return slnfile

    def dia_get_func_funcinfo(self, binfile):
        """Process the bin to get the info and function
        source_file functions here need to be debugged and fixed

        """
        binfile = binfile.replace("\\", "/")
        cmd_args = ["powershell", "-Command", "Dia2Dump", "-lines", "*", f"'{binfile}'"]
        file_cache = {}
        out, _err, exit_code = self.cmd_with_output(cmd_args)
        try:
            lines_notclean = out.decode().split("\r\n")
        except:
            logging.info("Dia2dump error")
            lines_notclean = []
        lines = []
        for line in lines_notclean:
            lines.append(line.strip())
        funcs_infos = {}
        rva_seg_length = 0
        dbg_seg_length = 0
        source_file = ""
        lines_infos = {}
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
                rva = (
                    re.findall(r"at \[\w+\]", line)[0]
                    .replace("at ", "")
                    .replace("[", "")
                    .replace("]", "")
                )
                length = int(re.findall(r"len \= \w+", line)[0].replace("len = ", ""), 16)
                line_number = int(re.findall(r"line \d+", line)[0].replace("line ", ""))
                lines_dict["line_number"] = line_number
                lines_dict["rva"] = rva
                lines_dict["length"] = length
                lines_dict["source_code"] = ""
                try:
                    source_file_cleaned = source_file.split(" (")[0]
                except Exception:
                    source_file_cleaned = source_file
                if source_file_cleaned not in file_cache:
                    try:
                        with open(source_file_cleaned) as source_f:
                            file_cache[source_file_cleaned] = source_f.readlines()
                    except Exception:
                        file_cache[source_file_cleaned] = []
                try:
                    lines_dict["source_code"] = file_cache[source_file_cleaned][line_number].strip()
                except Exception:
                    lines_dict["source_code"] = ""
                lines_dict["source_file"] = source_file_cleaned
                if "rva_start" not in func_name_infoitem:
                    func_name_infoitem["rva_start"] = rva
                if line_number > 10000000:
                    dbg_seg_length = dbg_seg_length + length
                rva_seg_length = rva_seg_length + length
                if not lines[i + 1].startswith("line"):
                    func_name_infoitem["rva_end"] = (
                        str(hex(int(rva, 16) + int(length))).replace("0x", "").rjust(len(rva), "0")
                    )
                    if rva_seg_length != 0:
                        func_name_infoitem["debug_ratio"] = (
                            str((dbg_seg_length / rva_seg_length) * 100)[:5] + "%"
                        )
                    else:
                        func_name_infoitem["debug_ratio"] = "0%"
                    if func_name in funcs_infos:
                        funcs_infos[func_name].append(func_name_infoitem)
                    else:
                        funcs_infos[func_name] = [func_name_infoitem]
                if func_name in lines_infos:
                    lines_infos[func_name].append(lines_dict)
                else:
                    lines_infos[func_name] = [lines_dict]
        return funcs_infos, lines_infos, source_file

    def run_build(self, repo, clone_dir, build_mode, compiler_flag="", slnfile=None, num_p_job=16):
        """Generate cmd to execute"""
        if not slnfile:
            logger.error("No sln file provided")
            return "No SLN File provided", BuildStatus.FAILED
        cmd = ["powershell", "-Command", "msbuild"]
        if build_mode in ["Release", "Debug"]:
            logger.debug(f"Adding property configuation={build_mode}")
            cmd.append(f"/property:Configuration={build_mode}")
        if self.library == "x86":
            cmd.append("/property:Platform=x86")
        elif self.library == "x64":
            cmd.append("/property:Platform=x64")
        elif self.library == "Mixed Platforms":
            cmd.append("/property:Platform='Mixed Platforms'")
        elif self.library == "Any CPU":
            cmd.append("/p:Platform=Any CPU")
        if self.compiler_version in ["v140", "v141"]:
            cmd.append(f"/p:WindowsTargetPlatformVersion={self.compiler_version}")

        # Map compiler_flag string to MSVC optimization property
        _MSVC_OPT_MAP = {
            "/Od": "/p:Optimization=Disable",
            "/O1": "/p:Optimization=MinSpace",
            "/O2": "/p:Optimization=MaxSpeed",
            "/Ox": "/p:Optimization=Full",
        }
        if build_mode == "Release" and compiler_flag in _MSVC_OPT_MAP:
            if compiler_flag != "/Od":
                cmd.append("/p:Optimize=true")
            cmd.append(_MSVC_OPT_MAP[compiler_flag])
        cmd.append("/maxcpucount:16")
        # cmd.append("/property:PostBuildEvent= ")
        # if target_dir:
        #     cmd.append(f"/property:OutDir={target_dir}") # change this so just go straight to successes/name?
        # if dfkjakl
        # cmd.append(f"/property:InDir={target_dir}) #
        cmd.append(f"'{slnfile}'")
        cmd = " ".join(cmd)
        logger.info("Windows cmd generated: %s", cmd)
        out, err, exit_code = self.cmd_with_output(cmd, 600)
        return_code = BuildStatus.SUCCESS if exit_code == 0 else BuildStatus.FAILED
        if return_code == BuildStatus.SUCCESS:
            logger.warning(f"BUILD STATUS FOR {repo} succeeded!!!")
        return out.decode() + err.decode(), return_code

    def post_build_hook(
        self,
        dest_binfolder,
        build_mode,
        repoinfo,
        compiler_flag,
        commit_hexsha,
        original_files=None,
    ):
        """Postprocess the pdb"""
        logger.debug(f"Adding files in {dest_binfolder}")
        bin_files = self.find_binaries(dest_binfolder)
        outer_list = []
        for _, binfile in enumerate(bin_files):
            binfile_path = os.path.join(dest_binfolder, binfile)
            # logging.info("Checking binary info %s: %s", binfile,
            #              os.path.isfile(binfile))
            funcs_infos, lines_infos, source_file = self.dia_get_func_funcinfo(binfile_path)
            # note to future dev, this seems to only really work for .pdb files
            item_dict = {}
            item_dict["functions"] = []
            # figure out how to just get filename/ not include C:/binaries/projects/ at least too
            item_dict["file"] = binfile
            for func_name, infos in funcs_infos.items():
                functions_val = {}
                functions_val["function_name"] = func_name
                functions_val["source_file"] = source_file
                if len(infos) == 1:
                    functions_val["intersect_ratio"] = "0%"
                else:
                    rva_segs = []
                    for info_dict in infos:
                        rva_segs.append((info_dict["rva_start"], info_dict["rva_end"]))
                    rva_segs.sort()
                    rva_len = int(rva_segs[-1][1], 16) - int(rva_segs[0][0], 16)
                    rva_gap = 0
                    for k in range(len(rva_segs) - 1):
                        rva_gap += int(rva_segs[k + 1][0], 16) - int(rva_segs[k][1], 16)
                    functions_val["intersect_ratio"] = str((rva_gap / rva_len) * 100)[:5] + "%"
                functions_val["function_info"] = funcs_infos[func_name]
                functions_val["lines"] = lines_infos[func_name]
                item_dict["functions"].append(functions_val)
            outer_list.append(item_dict)
        return outer_list
