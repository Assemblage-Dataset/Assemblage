"""
a vcpkg cluster


"""

import logging
import requests
import os
import shutil
import json
from assemblage.api import *

aws_profile = AWSProfile("assemblage-vcpkg", "assemblage")

def vcpkg_build_sys_hook(_files):
    """
    vcpkg source repo all can only been built using vcpkg
    (just for now, we need track github histroy of these repo)
    """
    return "vcpkg"

class VcpkgIORepos(DataSource):

    package_url = "https://vcpkg.io/output.json"

    def __init__(self, build_sys_callback) -> None:
        super().__init__(build_sys_callback)
        package_html = requests.get(self.package_url).json()
        self.repositories = package_html['Source']

    def fetch_data(self):
        """
        vcpkg repo actually only need name for building, everything except for name
        are dummy
        """
        for repo in self.repositories:
            yield {
                'name': repo['Name'],
                'url': repo['Name'],
                'language': "c/c++",
                'owner_id': 0,
                'description': "vcpkg_default_desc",
                'created_at': "2023-3-6 12:12:12",
                'updated_at': "2023-3-6 12:12:12",
                'size': 1000,
                'build_system': "vcpkg",
                # 'branch': repo["default_branch"]
            }, []
        logging.info("Finished!!")


class VcpkgBuild(BuildStartegy):

    def is_valid_binary(self, binary_path) -> bool:
        return binary_path.lower().endswith("exe") or binary_path.lower().endswith("dll") or binary_path.lower().endswith("pdb")  

    def clone_data(self, repo) -> Tuple[bytes, int, str]:
        """ vcpkg don't need clone, pass the final result dir as clone dir """
        # vcpkg packge name is also stored in 'url' because of scraper code
        dest_path = f"Builds/{repo['url']}"
        if os.path.exists(dest_path):
            shutil.rmtree(dest_path, ignore_errors=False, onerror=None)
        os.makedirs(os.path.join(dest_path, "triplets"))
        logging.info("Clone called")
        return b'No need for clone', BuildStatus.SUCCESS, dest_path
    
    def post_build_hook(self, dest_binfolder, build_mode, library, repoinfo, toolset,
                        optimization):
        """ Postprocess the pdb """
        bin_files = dia_list_binaries(dest_binfolder)
        outer_list = []
        for _, binfile in enumerate(bin_files):
            binfile_path = os.path.join(dest_binfolder, binfile)
            # logging.info("Checking binary info %s: %s", binfile,
            #              os.path.isfile(binfile))
            funcs_infos, lines_infos, source_file = dia_get_func_funcinfo(
                binfile_path)
            item_dict = {}
            item_dict["functions"] = []
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
                        rva_segs.append(
                            (info_dict["rva_start"], info_dict["rva_end"]))
                    rva_segs.sort()
                    rva_len = int(rva_segs[-1][1], 16) - int(rva_segs[0][0], 16)
                    rva_gap = 0
                    for k in range(0, len(rva_segs) - 1):
                        rva_gap += int(rva_segs[k+1][0], 16) - \
                            int(rva_segs[k][1], 16)
                    functions_val["intersect_ratio"] = str(
                        (rva_gap / rva_len) * 100)[:5] + "%"
                functions_val["function_info"] = funcs_infos[func_name]
                functions_val["lines"] = lines_infos[func_name]
                item_dict["functions"].append(functions_val)
            outer_list.append(item_dict)
        try:
            json_di = {}
            json_di["Platform"] = library
            json_di["Build_mode"] = build_mode
            json_di["Toolset_version"] = toolset
            json_di["URL"] = repoinfo["url"]
            json_di["Binary_info_list"] = outer_list
            json_di["Optimization"] = optimization
            json_di["Pushed_at"] = repoinfo["updated_at"]
            with open(os.path.join(dest_binfolder, PDBJSONNAME), "w") as outfile:
                json.dump(json_di, outfile, sort_keys=False)
            repoid = dest_binfolder.split("\\")[-1]
            # with open(os.path.join(PDBPATH, f"{repoid}.json"), "w") as outfile:
            #     json.dump(json_di, outfile, sort_keys=False, indent=4)
        except FileNotFoundError:
            logging.info("Pdbjsonfile not found")

    def run_build(self, repo, target_dir, build_mode, library, optimization,
                     slnfile, platform, compiler_version):
        """"""
        logging.info(f" >>> Building {repo} ...")
        triplet_cpu_arch = "x64" if library=="x64" else "x86"
        triplet_path = os.path.relpath(os.path.join(target_dir, "triplets", f"{triplet_cpu_arch}-windows.cmake"))
        triplet_flags = {"VCPKG_TARGET_ARCHITECTURE": triplet_cpu_arch, "VCPKG_CRT_LINKAGE":"dynamic", "VCPKG_LIBRARY_LINKAGE":"dynamic"}
        if build_mode.lower()=="release":
            triplet_flags["VCPKG_BUILD_TYPE"] = "release"
        triplet_flags["CMAKE_CXX_FLAGS"] = f"/{optimization}"
        triplet_flags["CMAKE_C_FLAGS"] = f"/{optimization}"
        with open(triplet_path, "w") as f:
            for x in triplet_flags:
                f.write(f"set({x} {triplet_flags[x]})\n")
        triplet_cpu_arch = "x86"
        triplet_path = os.path.relpath(os.path.join(target_dir, "triplets", f"{triplet_cpu_arch}-windows.cmake"))
        triplet_flags = {"VCPKG_TARGET_ARCHITECTURE": triplet_cpu_arch, "VCPKG_CRT_LINKAGE":"dynamic", "VCPKG_LIBRARY_LINKAGE":"dynamic"}
        if build_mode.lower()=="release":
            triplet_flags["VCPKG_BUILD_TYPE"] = "release"
        triplet_flags["CMAKE_CXX_FLAGS"] = f"/{optimization}"
        triplet_flags["CMAKE_C_FLAGS"] = f"/{optimization}"
        with open(triplet_path, "w") as f:
            for x in triplet_flags:
                f.write(f"set({x} {triplet_flags[x]})\n")


        cmd = f"vcpkg install {repo['url']} --overlay-triplets={target_dir}/triplets --x-install-root={target_dir} --allow-unsupported"
        logging.info(cmd)
        os.system(cmd)
        return b"1", b"1", b"1"


test_cluster_vcpkg = AssmeblageCluster(name="test", coordinator_addr="54.193.86.242"). \
                build_system_analyzer(vcpkg_build_sys_hook). \
                aws(aws_profile). \
                message_broker("54.193.86.242"). \
                builder(
                    platform="windows", compiler="vcpkg", build_opt=1,
                    custom_build_method=VcpkgBuild(),
                    aws_profile= aws_profile).\
                build_option(
                    1, platform="windows", language="c++",
                    library="x64",
                    build_command="Release",
                    compiler_name="vcpkg",
                    build_system="vcpkg")

test_cluster_vcpkg.boot()
