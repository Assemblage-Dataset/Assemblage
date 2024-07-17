import os
import logging
import subprocess
from assemblage.worker.build_method import cmd_with_output, windows_pre_processing, post_processing_pdb
from git import Repo


logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                    )


class Chromium:

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
        print("Please follow instructions to setup the env on this link: ")
        print("https://chromium.googlesource.com/chromium/src/+/main/docs/windows_build_instructions.md")

        if not os.path.isdir(self.clonedir):
            os.system(f"mkdir {self.clonedir}")
            os.system(f"cd {self.clonedir} && fetch chromium")
        
        repo = Repo(os.path.join(self.clonedir, "chromium", "src"))
        for tag in self.tags:
            try:
                repo.git.checkout(tag, force=True)
            except Exception as e:
                logging.error("Tag not found %s for chromium, err %s", tag, str(e))
                continue
            logging.info("Chromium checkout to release tag: %s", tag)
            self.repo_current_commit_hash = repo.head.commit.hexsha

            random_dir = tag
            if self.build_mode == "debug":
                cmd_with_output(rf"cd {os.path.join(self.clonedir, 'chromium', 'src')}&&gn gen --args='is_debug=true' out\{random_dir}")
            else:
                os.system(rf"cd {os.path.join(self.clonedir, 'chromium', 'src')}&&gn gen --args='is_debug=false' out\{random_dir}")
            os.system(rf" autoninja -C out\{random_dir} chrome")

            if len(os.listdir(os.path.join(self.clonedir, "out", random_dir))) > 2:
                logging.info("Build successful")
        
            self.post_build_hook(
                os.path.join(self.clonedir, "out", random_dir), self.build_mode, library=self.arch, 
                    repoinfo={"url":self.project_git_url, "updated_at": self.repo_current_commit_hash}, 
                    toolset=self.compiler_version,
                    optimization=self.optimization, 
                    source_codedir=self.clonedir, 
                    commit=self.repo_current_commit_hash, 
                    movedir=f"{self.collect_dir}/{self.project_git_url.split('/')[-1]}-{self.arch}-{self.optimization}-{tag}({self.compiler_version})")
        