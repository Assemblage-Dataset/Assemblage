"""
A test cluster template

- a rust binary cluster
- a gllvm c binary cluster

"""

import glob
import json
import logging
import os
import time

from assemblage.api import *


time_now = int(time.time())
start = time_now - time_now % 86400
querylap = 14400

def get_build_system(files):
    """Analyze build tool from file list"""
    build_systems = {"make": ["makefile"],
                     "cmake": ["cmakelists.txt"],
                     "cargo": ["Cargo.toml"]
                     }
    build_tools_list = []
    for fname in files:
        for build_tool, file_keywords in build_systems.items():
            for file_keyword in file_keywords:
                if file_keyword.lower() in fname.strip().lower():
                    build_tools_list.append(build_tool)
    build_tools = list(set(build_tools_list))
    if len(build_tools_list) == 0:
        return "others"
    else:
        return "/".join(build_tools)


# define scraper data source
github_c_repos = GithubRepositories(
    git_token="",
    qualifier={
        "language:c",
        "stars:>2"
    }, 
    crawl_time_start= start,
    crawl_time_interval=querylap,
    crawl_time_lap=querylap,
    proxies=[],
    build_sys_callback=get_build_system
    # sort="stars", order="desc"
)

github_rust_repo = GithubRepositories(
    git_token="",
    qualifier={
        "language:rust",
        "topic:rust",
        # "stars:>10"
    }, 
    crawl_time_start= start,
    crawl_time_interval=querylap,
    crawl_time_lap=querylap,
    build_sys_callback=get_build_system,
    proxies=[],
)

aws_profile = AWSProfile("assemblage-test", "assemblage")

class GllvmBuild(BuildStartegy):
    def run_build(self, repo, target_dir, build_mode, library,
                    optimization, slnfile,
                    platform, compiler_version) -> str:
        """ how to constuct a build command  """
        files = []
        for filename in glob.iglob(target_dir + '**/**', recursive=True):
            files.append(filename.split("/")[-1])
        logging.info("%s files in repo", len(files))
        build_tool = get_build_system(files)
        cmd = ""
        if 'bootstrap' in build_tool:
            cmd = f'cd {target_dir} && ./bootstrap && ' \
                'CC=gclang bash ./configure && CC=gclang timeout 1d make -j8'
        elif 'configure' in build_tool:
            cmd = f'cd {target_dir} && CC=gclang bash ./configure && ' \
                'CC=gclang timeout -s SIGKILL 1d make -j8'
        elif 'cmake' in build_tool:
            cmd = f'cd {target_dir} && CC=gclang cmake -Bbuild ./ && cd build && ' \
                'CC=gclang timeout -s SIGKILL 1d make -j8'
        elif 'make' in build_tool:
            cmd = f'cd {target_dir} && CC=gclang timeout -s SIGKILL 1d make -j16'
        logging.info("Linux cmd generated: %s", cmd)
        return cmd_with_output(cmd, 600, platform)

class RustBuild(BuildStartegy):
    def run_build(self, repo, target_dir, build_mode, library, optimization,
                     slnfile, platform, compiler_version):
        """ just cargo build """
        cmd = f"cd {target_dir} && RUSTFLAGS=-g cargo build --release"
        return cmd_with_output(cmd, 600, platform)
    
    def is_valid_binary(self, binary_path):
        if 'build_script_build' in binary_path or 'build-script-build' in binary_path \
            or 'build_script_main' in binary_path \
            or 'build-script-main' in binary_path:
            return False
        return True

def compute_function_boundary_binaryninja(bin_path):
    function_end_map = []
    logging.info("Analysis binary %s", bin_path)
    import binaryninja
    with binaryninja.open_view(bin_path) as bv:
        for func in bv.functions:
            function_end_map.append(
                {"name": func.name,
                 "ranges": [[ar.start, ar.end] for ar in func.address_ranges]}
            )
    logging.info("functuon entries %d", len(function_end_map))
    return function_end_map

def extract_function_bound_objdump(binary_file):
    """ this is for linux binary ground truth """
    function_end_map = []
    objdump_cmd = f"objdump -t -f {os.path.realpath(binary_file)} | grep 'F .text' | sort"
    out, err, code = cmd_with_output(objdump_cmd)
    return str(out)

class FunctionBoundaryAnalysis(PostAnalysis):
    """ analysis calculate function boundary inside a binary file """
    # TODO: store this as an internal provided analysis in Assemblage API

    def analysis(self, bin_file, analysis_out_dir):
        fname = os.path.basename(bin_file)
        pdb_json = os.path.join(os.path.dirname(bin_file), "pdb_info.json")
        function_bound_map = compute_function_boundary_binaryninja(bin_file)
        with open(f"{analysis_out_dir}/{fname}_funcbound.json", "w+") as f:
            f.write(json.dumps(function_bound_map))
        with open(f"{analysis_out_dir}/{fname}_funcbound.objdump", "w+") as f:
            f.write(extract_function_bound_objdump(bin_file))

# docker image used must contain `zip unzip wget git`

test_cluster_c = AssmeblageCluster(name="test"). \
                aws(aws_profile). \
                docker_network("assemblage-net", True). \
                message_broker(). \
                mysql(). \
                scraper([github_c_repos]). \
                build_option(
                    1, platform="linux", language="c", 
                    compiler_name="gclang",
                    build_system="make"). \
                build_option(
                    2, platform="linux", language="c", 
                    compiler_name="gclang",
                    build_system="cmake"). \
                builder(
                    platform="linux", compiler="gclang", build_opt=1,
                    docker_image="stargazermiao/gllvm",
                    custom_build_method=GllvmBuild(),
                    aws_profile= aws_profile). \
                builder(
                    platform="linux", compiler="gclang", build_opt=2,
                    docker_image="stargazermiao/gllvm",
                    custom_build_method=GllvmBuild(),
                    aws_profile= aws_profile). \
                post_processor(
                    name="function_boundary",
                    analysis=FunctionBoundaryAnalysis("function_boundary"),
                    opt_id=1,
                    docker_image="stargazermiao/bn"
                ). \
                use_new_mysql_local()


test_cluster_rust = AssmeblageCluster(name="test"). \
                build_system_analyzer(get_build_system). \
                aws(aws_profile). \
                docker_network("assemblage-net", True). \
                message_broker(). \
                mysql(). \
                scraper([github_rust_repo]). \
                build_option(
                    1, platform="linux", language="rust", 
                    compiler_name="rustc",
                    build_system="cargo"). \
                builder(
                    platform="linux", compiler="rustc", build_opt=1,
                    docker_image="stargazermiao/rust",
                    custom_build_method=RustBuild(),
                    aws_profile= aws_profile). \
                post_processor(
                    name="function_boundary",
                    analysis=FunctionBoundaryAnalysis("function_boundary"),
                    opt_id=1,
                    docker_image="stargazermiao/bn"
                ). \
                use_new_mysql_local()

test_cluster_c.boot()