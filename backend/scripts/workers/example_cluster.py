"""
A test cluster template

- a rust binary cluster
- a gllvm c binary cluster

"""

import glob
import logging
import os
import time
from assemblage.bootstrap import AssemblageCluster
from assemblage.consts import BuildStatus
from assemblage.worker.scraper import GithubRepositories, DataSource
from assemblage.worker.profile import AWSProfile
from assemblage.worker.postprocess import PostAnalysis
from assemblage.worker.build_method import BuildStrategy, DefaultBuildStrategy
from assemblage.worker.build_method import cmd_with_output
# from assemblage.config import Settings
# settings = Settings()


time_now = int(time.time())
start = time_now - time_now % 86400
querylap = 14400


# logging.basicConfig(
#     level=logging.INFO,
#     format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
#     datefmt='%Y-%m-%d %H:%M:%S',
# )


logger = logging.getLogger(__name__)


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
# github_c_repos = GithubRepositories(
#     git_token= os.getenv("GITHUB_TOKEN"),
#     qualifier={
#         "language:c++",
#         # "stars:>1"
#     }, 
#     crawl_time_start= start,
#     crawl_time_interval=querylap,
#     proxies=[],
#     build_sys_callback=lambda x: 'all'
# )
aws_profile = AWSProfile("assemblage-test", "assemblage")

# class SampleBuild(BuildStrategy):

#     def clone_data(self, repo):
#         clonedir = f"/tmp/{os.urandom(8).hex()}"
#         out, err, exit_code = cmd_with_output(f'git clone {repo["url"]} {clonedir}', 600, "linux")
#         return_code = BuildStatus.SUCCESS if exit_code == 0 else BuildStatus.FAILED
#         return out, return_code, clonedir


#     def run_build(self, repo, target_dir, compiler_version,
#                     library, build_mode,
#                     optimization, platform, slnfile):
#         """ how to constuct a build command  """
#         files = []
#         for filename in glob.iglob(target_dir + '**/**', recursive=True):
#             files.append(filename.split("/")[-1])
#         logger.info("%s files in repo", len(files))
#         build_tool = get_build_system(files)
        
        
#          # this should catch both c and c++ now
#         if settings.SAVE_ASSEMBLY:
#             extra_flags = 'CFLAGS="$CFLAGS -save-temps=obj" CXXFLAGS="$CXXFLAGS -save-temps=obj"'
#             logger.info(f"Saving .s and .o files as well: {extra_flags}")
#         else:
#             extra_flags = 'CFLAGS="$CFLAGS" CXXFLAGS="$CXXFLAGS"'
        
#         # TODO: update this to maybe not pick make if no make file, just attempt g++ / or something??
#         cmd = f'cd {target_dir} && make {extra_flags} -j16'
#         logger.info("Linux cmd generated: %s", cmd)
#         logger.info("Files found %s", os.listdir(target_dir)) 
#         out, err, exit_code = cmd_with_output(cmd, 600, platform)
#         return_code = BuildStatus.SUCCESS if exit_code == 0 else BuildStatus.FAILED
        
        
#         if return_code == BuildStatus.SUCCESS:
#             logger.warning(f"BUILD STATUS FOR {repo} succeeded!!!")
#         return out.decode() + err.decode(), return_code

#     def post_build_hook(self, dest_binfolder, build_mode, library, repoinfo, toolset,
#                         optimization, commit_hexsha):
#         logger.info(os.listdir(dest_binfolder))
#         # this is where the build actually comes
#         logger.info("Maybe move files to some Docker mapped volume") # change this 
#         os.system(f"mv {dest_binfolder} /binaries/{repoinfo['name']}")
        

test_cluster_c = AssemblageCluster(name="sample"). \
                aws(aws_profile). \
                message_broker().build_option(
                    1, platform="linux", language="c++", 
                    compiler_name="clang",
                    build_system="all"). \
                builder(
                    platform="linux", compiler="clang", build_opt=1,
                    custom_build_method=DefaultBuildStrategy(),
                    aws_profile= aws_profile)
                
                # use_new_mysql_local()

test_cluster_c.boot()