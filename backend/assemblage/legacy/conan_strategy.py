"""
Conan-based build strategy for DeepHistory dataset reconstruction.

This module provides a BuildStrategy subclass that uses the Conan package manager
to build specific versions of C/C++ libraries on Windows with MSVC, producing
EXE/DLL/PDB files for binary analysis research.

The original build scripts were lost; this is a reconstruction based on the
DeepHistory paper and the output SQLite database schema.

Usage (standalone):
    python -m assemblage.legacy.conan_strategy --package sqlite3 --version 3.43.1

Usage (via Assemblage worker):
    TYPE=legacy_conan python scripts/start_worker.py
"""

import glob as globmod
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Tuple

from assemblage.constants import BINPATH
from assemblage.enums import BuildStatus

logger = logging.getLogger(__name__)

# Build modes matching the original DeepHistory dataset
DEEPHISTORY_BUILD_MODES = ["Debug", "RelWithDebInfo", "Release"]

# MSVC optimization flags used in the dataset
DEEPHISTORY_OPTIMIZATIONS = ["Od", "O1", "O2"]

# MSVC toolset versions used in the original dataset
# vc140=VS2015, vc141=VS2017, vc142=VS2019, vc143=VS2022
DEEPHISTORY_TOOLSETS = ["vc143"]  # Only vc143 available by default; add others after installing optional VS components

# CMake artifacts to filter out (not actual project binaries)
_CMAKE_ARTIFACTS = frozenset({
    "compilerc.exe", "compileridc.exe", "compileridc.lib",
    "compilercxx.exe", "compileridcxx.exe", "compileridcxx.lib",
    "cmtc_check.exe",
})

# Known VS Build Tools install paths (searched in order)
_VCVARSALL_PATHS = [
    r"C:\BuildTools\VC\Auxiliary\Build\vcvarsall.bat",
    r"C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvarsall.bat",
    r"C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvarsall.bat",
    r"C:\Program Files\Microsoft Visual Studio\2022\Professional\VC\Auxiliary\Build\vcvarsall.bat",
    r"C:\Program Files\Microsoft Visual Studio\2022\Enterprise\VC\Auxiliary\Build\vcvarsall.bat",
]


def _find_vcvarsall() -> str:
    """Find vcvarsall.bat on the system."""
    for path in _VCVARSALL_PATHS:
        if os.path.exists(path):
            return path
    # Try vswhere as fallback
    try:
        result = subprocess.run(
            [r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe",
             "-latest", "-find", r"VC\Auxiliary\Build\vcvarsall.bat"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return ""


def _detect_msvc_version_from_env() -> str:
    """Read MSVC version from the Conan default profile (already detected)."""
    profile_path = os.path.expanduser("~/.conan2/profiles/default")
    try:
        with open(profile_path) as f:
            for line in f:
                if line.strip().startswith("compiler.version="):
                    return line.strip().split("=", 1)[1]
    except FileNotFoundError:
        pass
    return "194"


def _detect_toolset_version() -> str:
    """Get VCToolsVersion from environment."""
    return os.getenv("VCToolsVersion", "unknown")


class ConanBuildStrategy:
    """
    Build strategy using Conan package manager to compile specific package
    versions with MSVC on Windows. Produces EXE/DLL/PDB artifacts.

    This does NOT extend the main Assemblage BuildStrategy to avoid coupling
    with the live pipeline. It reuses the PDB extraction logic from
    WindowsDefaultStrategy via composition when needed.
    """

    def __init__(self, output_base: str = None, conan_home: str = None):
        self.output_base = output_base or os.path.join(BINPATH, "deephistory")
        self.conan_home = conan_home or os.getenv("CONAN_HOME")
        self.vcvarsall = _find_vcvarsall()
        self.msvc_version = _detect_msvc_version_from_env()
        self.toolset_version = _detect_toolset_version()
        self.platform = "windows"

        os.makedirs(self.output_base, exist_ok=True)
        logger.info(
            f"ConanBuildStrategy initialized: MSVC {self.msvc_version}, "
            f"toolset {self.toolset_version}, output={self.output_base}, "
            f"vcvarsall={self.vcvarsall}"
        )

    def _vcvars_cmd(self, cmd: str) -> str:
        """Wrap a command with vcvarsall.bat to set up MSVC environment."""
        if self.vcvarsall and os.path.exists(self.vcvarsall):
            # Use cmd.exe to run vcvarsall then the actual command
            return f'cmd /C ""{self.vcvarsall}" amd64 && {cmd}"'
        return cmd

    def _run_cmd(self, cmd: str, cwd: str = None, timeout: int = 600,
                 needs_msvc: bool = True) -> Tuple[str, str, int]:
        """Run a shell command and return (stdout, stderr, exit_code)."""
        if needs_msvc:
            cmd = self._vcvars_cmd(cmd)
        logger.info(f"Running: {cmd[:200]}...")
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True,
                timeout=timeout, cwd=cwd
            )
            return result.stdout, result.stderr, result.returncode
        except subprocess.TimeoutExpired:
            logger.error(f"Command timed out after {timeout}s")
            return "", "TimeoutExpired", 1
        except Exception as e:
            logger.error(f"Command failed: {e}")
            return "", str(e), 1

    def _get_conan_cache_dir(self) -> str:
        """Get the Conan 2.x cache base directory."""
        if self.conan_home:
            return os.path.join(self.conan_home, "p")
        return os.path.expanduser("~/.conan2/p")

    def _collect_pdbs_from_cache(self, package_name: str) -> list[str]:
        """
        Find PDB files in the Conan build cache for a given package.
        Conan recipes typically don't package PDBs, so we grab them
        from the build directories.

        Scoped by package name prefix in the cache directory name to avoid
        collecting PDBs from unrelated packages.
        """
        cache_dir = self._get_conan_cache_dir()
        pdbs = []
        # Conan 2 cache structure: ~/.conan2/p/b/<pkgprefix><hash>/b/build/**/*.pdb
        # The dir name starts with a shortened form of the package name
        short_name = package_name.replace("-", "").lower()
        cache_b = Path(cache_dir) / "b"
        if not cache_b.exists():
            return pdbs
        for pkg_dir in cache_b.iterdir():
            if not pkg_dir.is_dir():
                continue
            # Match directory names that start with a prefix of the package name
            dirname = pkg_dir.name.lower()
            if not dirname.startswith(short_name[:4]):
                continue
            for entry in pkg_dir.glob("b/build/**/*.pdb"):
                fname = entry.name.lower()
                # Skip vc*.pdb compiler intermediates (e.g. vc143.pdb)
                if fname.startswith("vc") and len(fname) < 12:
                    continue
                pdbs.append(str(entry))
        return pdbs

    def _resolve_toolset(self) -> str:
        """Resolve the MSVC toolset version string (e.g. 'vc143')."""
        # From VCToolsVersion env var: 14.44.x -> vc144, but convention is vc14X where X = VS major
        vctv = os.getenv("VCToolsVersion", "")
        if vctv:
            parts = vctv.split(".")
            if len(parts) >= 2:
                # 14.44 -> vc143 (VS2022), 14.3x -> vc143, 14.2x -> vc142, etc.
                minor = int(parts[1]) if parts[1].isdigit() else 0
                if minor >= 40:
                    return "vc143"
                elif minor >= 30:
                    return "vc143"
                elif minor >= 20:
                    return "vc142"
                elif minor >= 10:
                    return "vc141"
                else:
                    return "vc140"
        # Fallback: derive from Conan's compiler.version
        cv = self.msvc_version  # e.g. "194"
        if cv.startswith("194") or cv.startswith("193"):
            return "vc143"
        elif cv.startswith("192"):
            return "vc142"
        elif cv.startswith("191"):
            return "vc141"
        elif cv.startswith("190"):
            return "vc140"
        return "vc143"

    def build_package(
        self,
        package_name: str,
        version: str,
        build_mode: str = "RelWithDebInfo",
        optimization: str = "Od",
        github_url: str = "",
    ) -> Tuple[str, BuildStatus, dict]:
        """
        Build a single package/version/build_mode/optimization combination
        using Conan. Builds shared libraries (DLLs) to match the original
        DeepHistory dataset.

        Returns:
            (output_dir, status, metadata_dict)
        """
        pkg_ref = f"{package_name}/{version}"
        toolset = self._resolve_toolset()
        logger.info(f"Building {pkg_ref} mode={build_mode} opt={optimization} toolset={toolset}")

        # Create output directory
        safe_name = package_name.replace("/", "_")
        out_dir = os.path.join(
            self.output_base, safe_name, version, build_mode, optimization
        )
        os.makedirs(out_dir, exist_ok=True)

        # Build the optimization flag for CMake
        msvc_opt_flag = f"/{optimization}"

        # Build shared libraries (DLLs) to match original dataset
        # The original DeepHistory had DLLs + import libs for each package
        shared_opt = f"-o {package_name}/*:shared=True"

        # Conan install + build command (Conan 2.x syntax)
        # tools.build:cflags/cxxflags injects extra compiler flags via CMakeToolchain
        conan_cmd = (
            f"conan install --requires={pkg_ref} "
            f"--build=missing "
            f"-s build_type={build_mode} "
            f"{shared_opt} "
            f"--output-folder={out_dir} "
            f"-c \"tools.build:cflags=['{msvc_opt_flag}']\" "
            f"-c \"tools.build:cxxflags=['{msvc_opt_flag}']\" "
            f"--deployer=full_deploy"
        )

        out, err, code = self._run_cmd(conan_cmd, timeout=1200)

        if code != 0:
            # Some packages don't support shared builds; fall back to static
            logger.warning(f"Shared build failed for {pkg_ref}, trying static...")
            conan_cmd_static = (
                f"conan install --requires={pkg_ref} "
                f"--build=missing "
                f"-s build_type={build_mode} "
                f"--output-folder={out_dir} "
                f"-c \"tools.build:cflags=['{msvc_opt_flag}']\" "
                f"-c \"tools.build:cxxflags=['{msvc_opt_flag}']\" "
                f"--deployer=full_deploy"
            )
            out, err, code = self._run_cmd(conan_cmd_static, timeout=1200)
            if code != 0:
                # Final fallback: no opt flags
                conan_cmd_fallback = (
                    f"conan install --requires={pkg_ref} "
                    f"--build=missing "
                    f"-s build_type={build_mode} "
                    f"--output-folder={out_dir} "
                    f"--deployer=full_deploy"
                )
                out, err, code = self._run_cmd(conan_cmd_fallback, timeout=1200)
                if code != 0:
                    logger.error(f"All build attempts failed for {pkg_ref}: {err[-500:]}")
                    return out_dir, BuildStatus.FAILED, {}

        # Collect all binaries from deployed output + PDBs from cache
        binaries = self._find_binaries(out_dir)
        cache_pdbs = self._collect_pdbs_from_cache(package_name)
        for pdb_path in cache_pdbs:
            binaries.append(pdb_path)

        if not binaries:
            logger.warning(f"No binaries found for {pkg_ref}")
            return "", BuildStatus.FAILED, {}

        # Build the identifier: md5(url)_platform_mode_toolset_opt
        import hashlib as _hl
        url_hash = _hl.md5((github_url or package_name).encode()).hexdigest()
        identifier = f"{url_hash}_x64_{build_mode}_{toolset}_{optimization}"

        # Create final output folder: <output_base>/<identifier>/
        final_dir = os.path.join(self.output_base, identifier)
        os.makedirs(final_dir, exist_ok=True)

        # Move/copy binaries into final dir with <identifier>_ prefix
        final_binaries = []
        for src in binaries:
            fname = os.path.basename(src)
            dest_name = f"{identifier}_{fname}"
            dest_path = os.path.join(final_dir, dest_name)
            try:
                shutil.copy2(src, dest_path)
                final_binaries.append(dest_name)
            except OSError as e:
                logger.warning(f"Failed to copy {src}: {e}")

        # Write assemblage_meta.json (format expected by Assemblage_dataset_cli)
        meta = {
            "Platform": "x64",
            "Build_mode": build_mode,
            "Toolset_version": toolset,
            "URL": github_url or f"https://conan.io/center/recipes/{package_name}",
            "Commit": version,
            "Optimization": optimization,
            "Pushed_at": "",
            "License": "",
            "package_name": package_name,
            "version": version,
            "conan_url": f"https://conan.io/center/recipes/{package_name}",
            "Binary_info_list": [],  # populated by Dia2Dump later
        }
        meta_path = os.path.join(final_dir, "assemblage_meta.json")
        with open(meta_path, "w") as f:
            json.dump(meta, f, indent=2)

        logger.info(f"Staged {len(final_binaries)} files in {identifier}")

        # Clean up the intermediate Conan deploy dir
        shutil.rmtree(out_dir, ignore_errors=True)

        status = BuildStatus.SUCCESS
        return final_dir, status, meta

    def _find_binaries(self, path: str) -> list[str]:
        """Find EXE, DLL, PDB, and LIB files in the output directory.
        Filters out CMake compiler-check artifacts (CompilerIdC.exe etc)."""
        results = []
        binary_exts = (".exe", ".dll", ".pdb", ".lib")
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in (".git", "__pycache__")]
            for f in files:
                if f.lower().endswith(binary_exts):
                    # Filter out CMake compiler-detection artifacts
                    if f.lower() in _CMAKE_ARTIFACTS:
                        continue
                    results.append(os.path.join(root, f))
        return results

    def extract_pdb_info(self, binary_path: str) -> dict:
        """
        Extract function/RVA/line info from a PDB file using Dia2Dump.
        Returns a dict with functions, rvas, and lines.

        This replicates the PDB extraction from WindowsDefaultStrategy.dia_get_func_funcinfo
        but as a standalone function for the legacy pipeline.
        """
        binary_path = binary_path.replace("\\", "/")
        cmd = f"powershell -Command \"Dia2Dump -lines * '{binary_path}'\""

        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=120
            )
            raw_lines = result.stdout.split("\r\n")
        except Exception as e:
            logger.warning(f"Dia2Dump failed for {binary_path}: {e}")
            return {"functions": {}, "lines": {}, "source_file": ""}

        lines = [l.strip() for l in raw_lines]
        funcs_infos = {}
        lines_infos = {}
        source_file = ""
        func_name = ""
        func_name_infoitem = {}
        rva_seg_length = 0
        dbg_seg_length = 0

        for i, line in enumerate(lines):
            if line.startswith("**"):
                func_name = line.replace("**", "").replace(" ", "").strip()
                rva_seg_length = 0
                dbg_seg_length = 0
                func_name_infoitem = {}

            if line.startswith("line"):
                if len(re.split(r"\w:\\", line)) == 2:
                    source_file = re.findall(r"\w:\\", line)[0] + re.split(r"\w:\\", line)[1]
                try:
                    rva = re.findall(r"at \[\w+\]", line)[0].replace("at ", "").replace("[", "").replace("]", "")
                    length = int(re.findall(r"len \= \w+", line)[0].replace("len = ", ""), 16)
                    line_number = int(re.findall(r"line \d+", line)[0].replace("line ", ""))
                except (IndexError, ValueError):
                    continue

                lines_dict = {
                    "line_number": line_number,
                    "rva": rva,
                    "length": length,
                    "source_code": "",
                    "source_file": source_file.split(" (")[0] if " (" in source_file else source_file,
                }

                if "rva_start" not in func_name_infoitem:
                    func_name_infoitem["rva_start"] = rva
                rva_seg_length += length

                next_is_line = (i + 1 < len(lines) and lines[i + 1].startswith("line"))
                if not next_is_line:
                    func_name_infoitem["rva_end"] = str(
                        hex(int(rva, 16) + length)
                    ).replace("0x", "").rjust(len(rva), "0")
                    if rva_seg_length != 0:
                        func_name_infoitem["debug_ratio"] = str(
                            (dbg_seg_length / rva_seg_length) * 100
                        )[:5] + "%"
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

        return {
            "functions": funcs_infos,
            "lines": lines_infos,
            "source_file": source_file,
        }


def _cli():
    """CLI entry point for testing individual package builds."""
    import argparse

    parser = argparse.ArgumentParser(description="Build a Conan package for DeepHistory")
    parser.add_argument("--package", required=True, help="Conan package name")
    parser.add_argument("--version", required=True, help="Package version")
    parser.add_argument("--build-mode", default="RelWithDebInfo",
                        choices=["Debug", "RelWithDebInfo", "Release"])
    parser.add_argument("--optimization", default="Od",
                        choices=["Od", "O1", "O2", "Ox"])
    parser.add_argument("--output", default=None, help="Output base directory")
    parser.add_argument("--github-url", default="", help="GitHub URL for metadata")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    strategy = ConanBuildStrategy(output_base=args.output)
    out_dir, status, meta = strategy.build_package(
        args.package, args.version, args.build_mode, args.optimization, args.github_url
    )
    print(f"Status: {status}")
    print(f"Output: {out_dir}")
    if meta:
        print(f"Binaries: {len(meta.get('binaries', []))}")


if __name__ == "__main__":
    _cli()
