import os
import logging
import subprocess
from assemblage.worker.build_method import cmd_with_output, windows_pre_processing, post_processing_pdb
from git import Repo


logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    )


class Pcre2:

    def __init__(self, 
                clonedir,
                collect_dir,
                project_git_url,
                optimization,
                build_mode,
                arch,
                tags,
                compiler_version="Visual Studio 15 2017",
                post_build_hook=post_processing_pdb):
        self.clonedir = clonedir
        self.collect_dir = collect_dir
        self.project_git_url = project_git_url
        self.optimization = optimization
        self.build_mode = build_mode
        self.tags = tags
        self.arch = arch
        self.compiler_version = compiler_version
        self.post_build_hook = post_build_hook
        self.repo_current_commit_hash = None



    def run(self):

        if not os.path.isdir(self.clonedir):
            os.system(f"git clone --recursive {self.project_git_url} {self.clonedir})")
        repo = Repo(self.clonedir)
        for tag in self.tags:
            try:
                repo.git.checkout("pcre2-"+tag, force=True)
            except Exception as e:
                logging.info("Tag not found %s, err %s", tag, str(e))
                continue
            logging.info("Tag found %s", tag)
            self.repo_current_commit_hash = repo.head.commit.hexsha

            random_dir = tag # or some random string
            cmd_with_output(f"mkdir {random_dir}", platform="windows", cwd=self.clonedir)
            cmd_with_output(f"git submodule update --recursive --remote", platform="windows", cwd=self.clonedir)

            arch4cmake = "x64"
            if self.arch == "x86":
                arch4cmake = "Win32"
            else:
                arch4cmake = "x64"
            out = cmd_with_output(f'cmake .. -G "{self.compiler_version}" -A {arch4cmake}', platform="windows", 
                                cwd=os.path.join(self.clonedir, random_dir))
            logging.info(out[1])
            assert out[0] == 0, "CMake failed"
            compiler_version_ms = "v1??"
            if "2022" in self.compiler_version:
                compiler_version_ms = "v143"
            elif "2019" in self.compiler_version:
                compiler_version_ms = "v142"
            elif "2017" in self.compiler_version:
                compiler_version_ms = "v141"
            elif "2015" in self.compiler_version:
                compiler_version_ms = "v140"
            msg, status, build_file = windows_pre_processing(
                self.library, self.build_mode,
                os.path.join(self.clonedir, random_dir), self.optimization, tmp_dir="", compiler_version=compiler_version_ms,
                favorsizeorspeed="", inlinefunctionexpansion="", intrinsicfunctions="")
            logging.info(msg)
            out = cmd_with_output(f"msbuild pcre2.sln", platform="windows", 
                                cwd=os.path.join(self.clonedir, random_dir))
            logging.info(out[1])
            assert out[0] == 0, "MSBuild failed"          
            self.post_build_hook(
		        os.path.join(self.clonedir, random_dir), self.build_mode, library=self.arch, 
                    repoinfo={"url":self.project_git_url, "updated_at": self.repo_current_commit_hash}, 
                    toolset=self.compiler_version,
                    optimization=self.optimization, 
                    source_codedir=self.clonedir, 
                    commit=self.repo_current_commit_hash, 
                    movedir=f"{self.collect_dir}/{self.project_git_url.split('/')[-1]}-{self.arch}-{self.optimization}-{tag}({self.compiler_version})")
        