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
from assemblage.worker.build_method import cmd_with_output

time_now = int(time.time())
start = time_now - time_now % 86400
querylap = 14400

def get_build_system(files):
    """Analyze build tool from file list"""
    # build_systems = {"make": ["makefile"],
    #                  "cmake": ["cmakelists.txt"],
    #                  "cargo": ["Cargo.toml"]
    #                  }
    # build_tools_list = []
    # for fname in files:
    #     for build_tool, file_keywords in build_systems.items():
    #         for file_keyword in file_keywords:
    #             if file_keyword.lower() in fname.strip().lower():
    #                 build_tools_list.append(build_tool)
    # build_tools = list(set(build_tools_list))
    # if len(build_tools_list) == 0:
    #     return "others"
    # else:
    #     return "/".join(build_tools)
    return "all"


# define scraper data source
github_c_repos = GithubRepositories(
    git_token="",
    qualifier={
        "language:c++",
        # "stars:>1"
    }, 
    crawl_time_start= start,
    crawl_time_interval=querylap,
    proxies=[],
    build_sys_callback=lambda x: 'all'
)
aws_profile = AWSProfile("assemblage-test", "assemblage")

class SampleBuild(BuildStartegy):

    def clone_data(self, repo):
        clonedir = os.urandom(8).hex()
        out, err, exit_code = cmd_with_output(f'git clone {repo["url"]} {clonedir}', 600, "linux")
        return_code = BuildStatus.SUCCESS if exit_code == 0 else BuildStatus.FAILED
        return out, return_code, clonedir



    def run_build(self, repo, target_dir, compiler_version,
                    library, build_mode,
                    optimization, platform, slnfile):
        """ how to constuct a build command  """
        files = []
        for filename in glob.iglob(target_dir + '**/**', recursive=True):
            files.append(filename.split("/")[-1])
        logging.info("%s files in repo", len(files))
        build_tool = get_build_system(files)
        cmd = f'cd {target_dir} && make -j16'
        logging.info("Linux cmd generated: %s", cmd)
        logging.info("Files found %s", os.listdir(target_dir))
        out, err, exit_code = cmd_with_output(cmd, 600, platform)
        return_code = BuildStatus.SUCCESS if exit_code == 0 else BuildStatus.FAILED
        return out.decode() + err.decode(), return_code

    def post_build_hook(self, dest_binfolder, build_mode, library, repoinfo, toolset,
                        optimization, commit_hexsha):
        print(os.listdir(dest_binfolder))

test_cluster_c = AssmeblageCluster(name="sample"). \
                aws(aws_profile). \
                docker_network("assemblage-net", True). \
                message_broker(). \
                mysql(). \
                scraper([github_c_repos]). \
                build_option(
                    1, platform="linux", language="c++", 
                    compiler_name="gcc",
                    build_system="all"). \
                builder(
                    platform="linux", compiler="gcc", build_opt=1,
                    custom_build_method=SampleBuild(),
                    aws_profile= aws_profile). \
                use_new_mysql_local()

test_cluster_c.boot()