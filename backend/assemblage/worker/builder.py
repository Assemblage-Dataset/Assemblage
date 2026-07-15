"""
Assemblage Worker Node
1. clone repo
2. build repo
3. collect binary file
Yihao Sun

2025
Alex Duly
"""

import glob
import json
import logging
import ntpath
import os
import shutil
import signal
import stat
import sys
import tempfile
import time
from pathlib import Path


def _sigterm_handler(signum, frame):
    """Exit immediately on SIGTERM so docker stop works."""
    logging.getLogger(__name__).info("Received SIGTERM, exiting")
    os._exit(0)


signal.signal(signal.SIGTERM, _sigterm_handler)

from assemblage.config import BuilderSettings
from assemblage.consts import (
    BINPATH,
    MAX_MQ_SIZE,
    TASK_TIMEOUT_THRESHOLD,
    BuildStatus,
    CloneStatus,
    InputQueue,
    OutputQueue,
    SupportedPlatform,
    WorkerType,
)
from assemblage.mq.client import Connection, MQQueue
from assemblage.mq.messages import (
    BinaryTaskMsgIn,
    BuilderRegIn,
    BuilderRegOut,
    BuilderTaskOut,
    BuildStatusMsgIn,
    CloneStatusMsgIn,
    MQMsg,
    PostAnalysisTaskMsgIn,
)
from assemblage.s3.client import S3Bucket, S3Client
from assemblage.worker.base_worker import BasicWorker
from assemblage.worker.build_method import LinuxBuildStrategy, WindowsDefaultStrategy

logger = logging.getLogger(__name__)


NON_EXE_MODE = stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH

TEMP_DIR = Path(tempfile.gettempdir())


class Builder(BasicWorker):
    """
    A Worker that clones and builds repositories.
    It places built binaries in a target directory given by the task.
    """

    def __init__(
        self,
        settings: BuilderSettings,  # generic builder settings class,
        # keep for now i thik this sets the build opt from the table?  - change to be included in message from coordinator...
        tmp_dir="/tmp/",
        rand_build=False,
        random_pick=0,
        blacklist=None,
        proxy_clone_servers=None,
        proxy_token="",
        #  send_binary_method="s3"
        aws_profile=None,
    ):
        super().__init__(
            settings.name, settings.mq_host, settings.mq_port, worker_type=WorkerType.Builder
        )
        self.platform = settings.build_os

        self.library = settings.library  # x64 vs x86. architecture might be better name
        self.build_opt_queue = None
        self.opt_id = None
        self.build_opt_queue_args = {
            "arguments": {
                "x-max-length": MAX_MQ_SIZE,
                "x-overflow": "reject-publish",
                "x-message-ttl": TASK_TIMEOUT_THRESHOLD,
            }
        }
        if blacklist:  # what is this
            self.blacklist = blacklist
        else:
            self.blacklist = []
        # "S3" method will utilize the credentials found in
        # `~/.aws/credentials`. Use "FTP" if you want to connect
        # to a local FTP server instead.
        # self.aws_profile = aws_profile  # probably strip and rewrite for minio?
        self.rand_build = rand_build  # what?
        self.library = settings.library
        self.random_pick = random_pick  # what is this?
        #  a repo keep track of the (URL, opt_id) built before
        self.built_b_status_list = []  # is this used?
        self.tmp_dir = os.path.realpath(tmp_dir)  # is this needed now?
        if not os.path.exists(self.tmp_dir):
            os.mkdir(self.tmp_dir)
        self.clone_proxy_servers = proxy_clone_servers  # what?
        self.clone_proxy_token = proxy_token  # what?
        # self.build_callback = build_method.default_build_command_generator

        # compiler_flag is set from COMPILER_FLAG env var (e.g. "-O0", "-O1", "-O2", "-O3")
        self.compiler_flag = settings.compiler_flag or None
        self.build_command = None

        self.MAX_WAIT = settings.WAIT_FOR_BUILD_OPT  # 15 minutes in seconds
        self.CHECK_INTERVAL = settings.CONFIG_CHECK_INTERVAL  # check every 5 seconds

        self.logging_build_fails = 0
        self.logging_build_successes = 0
        self.logging_projects_processed = 0

        # s3 configuration
        if settings.s3_enabled:
            # settings.validate_s3()
            self.s3_client = S3Client(
                host=settings.S3_HOST,
                port=settings.S3_PORT,
                access_key=settings.S3_ACCESS_KEY,
                secret_access_key=settings.S3_SECRET_ACCESS_KEY,
                https=settings.S3_HTTPS,
                region_name=settings.S3_REGION,
            )
            # coordindator creates but then only needs read only ( unless used to delete ) - leave for now.
            # stores cloned projects
            self.ProjectBucket = S3Bucket(self.s3_client, "project-archive")
            # store build artifacts
            self.ArtifactBucket = S3Bucket(self.s3_client, "artifacts")
            base_path = TEMP_DIR
        else:
            self.s3_client = None
            self.ProjectBucket = None
            self.ArtifactBucket = None
            base_path = BINPATH

        if self.platform == SupportedPlatform.LINUX:
            self.build_system = "all"  # i think this is the default?
            #  maybe filter by language here too
            self.build_strategy = LinuxBuildStrategy(
                # rename to linux build strat? and add compilier flags but eh for now
                compiler=settings.compiler,
                language=settings.language,
                save_assembly=settings.save_assembly,
                library=self.library,
                base_path=base_path,
            )
        elif self.platform == SupportedPlatform.WINDOWS:
            self.build_system = "sln"  # i think this is the default?
            self.build_strategy = WindowsDefaultStrategy(
                compiler=settings.compiler,
                language=settings.language,
                save_assembly=settings.save_assembly,
                library=self.library,
                base_path=base_path,
            )
        else:
            logger.error(
                f"Running on invalid platform: {self.platform}. Options are Linux or Windows"
            )
            sys.exit(1)

    def _clean_folder(self, path):
        """
        Try to clean up the target folder, if it fails, will walk and try adn deelte as much as possible.

        This is required due to the windows file lock on the produced executable. Only called on s3 when the projects are saved separately
        This does not delete the top level folder of the git username, but not worth the time and effort currenly
        """
        logger.info(f"Deleting {path}")
        if not os.path.exists(path):
            return

        try:
            shutil.rmtree(path)
            return  # success
        except Exception as e:
            logger.warning(f"rmtree failed for {path}: {e}.")

        for root, dirs, files in os.walk(path, topdown=False):
            for f in files:
                file_path = os.path.join(root, f)
                try:
                    os.chmod(file_path, stat.S_IWRITE)  # ensure writable
                    os.remove(file_path)
                except Exception as e:
                    logger.warning(f"Could not delete file {file_path}: {e}")

            for d in dirs:
                dir_path = os.path.join(root, d)
                try:
                    os.rmdir(dir_path)
                except Exception as e:
                    logger.warning(f"Could not delete directory {dir_path}: {e}")
        try:
            os.rmdir(path)
        except Exception as e:
            logger.warning(f"Could not delete folder {path}: {e}")

    def _process_window_file_names(self, filename: str):
        """
        Processes the window file names to icnlude release and architecture in filename, so not in subdir
        turns /x64/Debug/filename -> x64_debug_filename
        Only the basename is used; if no known build type or architecture is found, returns the original filename.

        """

        prefix = []
        if "debug" in filename.lower():
            prefix.append("debug")
        else:
            prefix.append("release")
        if "x86" in filename.lower():
            prefix.append("x86")
        if "x64" in filename.lower():
            prefix.append("x64")

        if prefix:
            prefix_str = "_".join(prefix)
            dest_file = f"{prefix_str}_{ntpath.basename(filename)}"
            return dest_file
        else:
            return filename  # nothing to change

    def run_ctrl(self):
        """
        At the moment, all this does is send a registering message to the coordinator.
        Then it waits for a response and then sets the build option queue to listen on.
        """
        try:
            conn: Connection = self.mq_client.create_connection(
                conn_name=f"{self}-{OutputQueue.BUILDER_CTRL}",
                channel_name=f"{self}-{OutputQueue.BUILDER_CTRL}",
            )
            while True:
                if not self.build_opt_queue:  # handle when errors happen in creating hte connectino/ consume without the builder having a queue - could expand to just do some of htis on start up
                    conn.ensure_connection()
                    conn.create_channel()
                    # Declare the control queue BEFORE sending registration
                    # so the coordinator can reply immediately
                    conn.add_queue(self.control_queue_in)
                    self.process_send_msg(kind=InputQueue.BUILD_REG, task=None)
                    logger.info(
                        "Registration Message sent. Starting consumption on control queue now"
                    )
                    # Use consume() directly, NOT start_consumer() — the
                    # control queue is auto_delete and one-shot. After
                    # control_message_handler calls stop_consuming, we're done.
                    conn.consume(self.control_queue_in)
                    logger.info(f"Control consume on {self} has finished.")
                else:
                    logger.info(f"Builder registered and listening on: {self.build_opt_queue}")
                time.sleep(15)

        except Exception as e:
            logger.error(f"Failed to create builder control thread, exec={e}")

    def run_job(self):
        """
        Run the build job.

        """

        # create input connection and channel
        # create input queue
        # start consuming

        logger.info(f"{self} Waiting for build_opt_thread to be set")
        # if not self.build_opt_queue:
        #     logger.info("Waiting for build_opt_thread to be set")

        start_time = time.time()

        while not self.sleep_job_event.wait(timeout=self.CHECK_INTERVAL):
            elapsed = (time.time() - start_time) / 60  # minutes

            if self.MAX_WAIT:
                if elapsed > self.MAX_WAIT:
                    logger.warning(
                        f"{self}: waited {elapsed:.0f}m — exiting after {self.MAX_WAIT} minutes timeout"
                    )
                    os._exit(1)

                logger.info(
                    f"{self}: still waiting for configuration ({elapsed:.0f}m elapsed). "
                    f"Will exit after {self.MAX_WAIT} minutes without configuration"
                )

            else:
                logger.info(f"{self}: still waiting for configuration ({elapsed:.0f}m elapsed)")
        logger.info(f"Build option queue set to {self.build_opt_queue} initialising job")
        conn: Connection = self.mq_client.create_connection(
            conn_name=f"{self}", channel_name=f"{self}"
        )
        conn.create_channel()
        logger.info(f"{self} Starting consumption on {self.build_opt_queue}")
        self.mq_client.start_consumer(conn=conn, queue=self.build_opt_queue, retry_delay=10)
        logger.warning(f"Consume build opt {self.build_opt_queue} on {self} has finished.")

    def control_message_handler(self, ch, method, props, body):
        """recieive a control message to specify the build option queue.
        Figure out later how to change the build options queue, and interrupt the job handler
        for this
        Also todo: figure out other commands/how to differentiate if necessary
        """
        if props.correlation_id != self.uuid:  # correlation ID doesnt match, send back onto queue
            ch.basic_nack(delivery_tag=method.delivery_tag, requeue=True)
            return
        logger.info("Recieiving builder information")
        # modifiy to include routing key + exhange name?
        msg = BuilderRegOut.from_json(body)
        logger.info(f"Recieived Builder Reg Info {msg}")

        self.opt_id = msg.build_opt_id
        self.build_opt_queue = MQQueue(
            msg.build_opt_queue,
            callback=self.job_handler,
            exchange_name=f"{OutputQueue.BUILD_OPT}",
            routing_key=f"{OutputQueue.BUILD_OPT}_{self.opt_id}",
        )
        ch.basic_ack(delivery_tag=method.delivery_tag)
        logger.info(f"Build {self.name} registered, waking job thread")
        self.sleep_job_event.set()
        # maybe add some heartbeat/ check that the job queue is running. if it isnt then restart?
        logger.info("Build opt setting, Stopping consumption on control message")
        ch.stop_consuming()

    def job_handler(self, ch, method, _props, body):
        """
        Callback for when we get a task request from a coordinator to build and clone/pull a project.
        """
        self.sleep_job_event.wait()  # way to get the control thread to block

        task = BuilderTaskOut.from_json(body)  # TODO: create type for this

        ch.basic_ack(method.delivery_tag)
        # check if this is an duplicate task
        # if time.time() - task['msg_time'] >= TASK_TIMEOUT_THRESHOLD: # not sure on this. is this because of a lag between starting the builder and coordinator??
        #     logger.info("Found duplicate build (%s, %d)",
        #                 task['url'], self.opt_id)
        #     self.process_send_msg(repo=task,
        #                   kind='clone',
        #                   url=task['url'],
        #                   status=BuildStatus.OUTDATED_MSG,
        #                   msg="duplicate")
        #     return

        time_start = time.time()

        logger.info("Received a task to build %s. buildsys: %s", task.url, task.build_system)
        self.logging_projects_processed += 1

        # Try to restore from S3 archive first; fall back to git clone
        restored_from_s3 = False
        clone_msg, clone_status, clone_dir = None, None, None
        commit_hexsha = ""

        if self.s3_client and self.ProjectBucket:
            username, project_name = self.build_strategy.parse_github_name(task.url)
            if username and project_name:
                commit_hexsha = task.commit_hexsha
                if not commit_hexsha:
                    commit_hexsha = self._read_latest_commit_pointer(username, project_name)
                if commit_hexsha:
                    archive_path = self._try_download_project(username, project_name, commit_hexsha)
                    if archive_path:
                        clone_msg, clone_status, clone_dir = (
                            self.build_strategy.restore_from_archive(archive_path, task.url)
                        )
                        if clone_status == CloneStatus.SUCCESS:
                            restored_from_s3 = True
                        try:
                            os.remove(archive_path)
                        except OSError:
                            pass

        if not restored_from_s3:
            logger.info(f" Cloning {task.url}...")
            clone_msg, clone_status, clone_dir = self.build_strategy.clone_data(task.url)

        original_files = []
        for filename in glob.iglob(clone_dir + "**/**", recursive=True):
            original_files.append(filename)

        time_clone_end = time.time()

        if clone_status == CloneStatus.SUCCESS:
            logger.info(f" Clone/restore SUCCESS for task '{task.name}'.")
            compiler_flag = self.compiler_flag
            compiler_version = self.build_strategy.compiler_version
            if task.commit_hexsha:
                commit_hexsha = task.commit_hexsha
            else:
                commit_hexsha = self.build_strategy.get_project_commit(clone_dir)

            # default save location - will be the host + save dir ( either s3 path or the builder it is on + the path in teh builder)
            save_path = f"{self}:{clone_dir}"

            if self.s3_client and not restored_from_s3:
                # First builder for this repo: upload archive + write pointer
                logger.info(f"Uploading {clone_dir} to s3 bucket {self.ProjectBucket}")
                username, project = clone_dir.rstrip("/").split("/")[-2:]
                saved = self.save_project_to_s3(clone_dir, username, project, commit_hexsha)
                if saved:
                    save_path = f"{self.ProjectBucket}/{username}/{project}/{commit_hexsha}.tar.gz"
                    logger.info(f"Project saved to {save_path}")
                    # Write pointer so other builders can find this archive
                    pointer_key = f"{username}/{project}/latest.txt"
                    self.ProjectBucket.put_bytes(pointer_key, commit_hexsha.encode())

            self.process_send_msg(
                task=task,
                kind=InputQueue.CLONE,
                url=task.url,
                status=clone_status,
                msg=self.uuid[:5] + clone_msg.decode(),
                commit_hexsha=commit_hexsha,
                save_path=save_path,
                build_time=0,
            )

            # this is currently only needed for windows, but linux just reutrns none too, so it wont break
            # seems cleaner to do it like this , instead of doing if statements here

            compiler_flag = self.compiler_flag or ""
            self.process_send_msg(
                task=task,
                kind=InputQueue.BUILD,
                url=task.url,
                status=BuildStatus.PROCESSING,
                msg="Received and building",
                commit_hexsha=commit_hexsha,
                build_time=0,
            )

            logger.info(f"Starting pre build with compiler_flag: {compiler_flag}")
            sln_file = self.build_strategy.pre_build(
                clone_dir=clone_dir, compiler_flag=compiler_flag
            )
            logger.info(f"Prebuild SUCCESS. Building {task.url}...")
            logger.info(f"Building '{task.name}' with flag {compiler_flag}...")
            before_build_time = int(time.time())
            build_msg, build_status = self.build_strategy.run_build(
                repo=task.url, clone_dir=clone_dir, slnfile=sln_file, compiler_flag=compiler_flag
            )

            after_build_time = int(time.time())
            dwarf_list = (
                self.build_strategy.post_build_hook(
                    clone_dir, task, compiler_flag, commit_hexsha, original_files=original_files
                )
                or []
            )
            logger.info(f"Build message: {build_msg}")

            logger.info(f"Build {build_status} for task '{task.name}' with flag {compiler_flag}.")

            all_builds_saved = True
            if build_status == BuildStatus.SUCCESS:
                # Generate and save metadata (includes Binary_info_list)
                metadata = self.generate_metadata(clone_dir, task, commit_hexsha, compiler_flag)
                if dwarf_list:
                    metadata["Binary_info_list"] = dwarf_list

                # Save metadata locally or to S3
                username, project = clone_dir.rstrip("/").split("/")[-2:]
                compiler = self.build_strategy.compiler
                if self.s3_client:
                    self.save_metadata_to_s3(
                        clone_dir,
                        username,
                        project,
                        commit_hexsha,
                        compiler,
                        compiler_flag,
                        metadata,
                    )
                else:
                    self.save_metadata_locally(clone_dir, commit_hexsha, compiler_flag, metadata)

                dest_binfolder, saved_successfully = self.save_binaries(
                    clone_dir,
                    task,
                    original_files=original_files,
                    commit_hexsha=commit_hexsha,
                    compiler_flag=compiler_flag,
                )
                self.logging_build_successes += 1

                if not saved_successfully:
                    all_builds_saved = False

                logger.info(f"Binaries saved to {dest_binfolder}")
            else:
                self.logging_build_fails += 1
                all_builds_saved = False
                logger.info(
                    f"Build failed for task '{task.name}' with flag {compiler_flag} err {build_msg[:500]}"
                )

            self.process_send_msg(
                task=task,
                kind=InputQueue.BUILD,
                url=task.url,
                status=build_status,
                msg="Build Process Finished",
                commit_hexsha=commit_hexsha,
                build_time=(after_build_time - before_build_time),
            )

            if self.s3_client and all_builds_saved:
                # dirname needed to also remove parent folder with username
                self._clean_folder(os.path.dirname(clone_dir))
            total_time_elapsed = round(time.time() - time_start, 3)
            clone_time_elapsed = round(time_clone_end - time_start, 3)
            logger.info(
                f"""Duration of task {task.name}: {total_time_elapsed}s ({clone_time_elapsed}s to clone, {round(total_time_elapsed - clone_time_elapsed, 3)}s to build)"""
            )
            if self.logging_projects_processed % 10 == 0:
                logger.info(f"""{self.logging_projects_processed} repos processed, {self.logging_build_fails + self.logging_build_successes} builds attempted 
                    ({self.logging_build_successes} successes, {self.logging_build_fails} failures)""")

        else:
            self.process_send_msg(
                task=task,
                kind=InputQueue.CLONE,
                url=task.url,
                status=clone_status,
                msg=self.uuid[:5] + clone_msg.decode(),
                commit_hexsha="",
                save_path=None,
            )

            logger.info("Clone FAILURE %s: %s", task.url, clone_msg)

        # Exit after every 1000 tasks for a clean restart
        if self.logging_projects_processed >= 1000:
            logger.info(
                "Worker %s reached %d tasks, exiting for clean restart",
                self.uuid[:5],
                self.logging_projects_processed,
            )
            os._exit(0)

    def generate_metadata(
        self, clone_dir: str, task, commit_hexsha: str, compiler_flag: str
    ) -> dict:
        """
        Generate metadata JSON for the build.

        Key names match what Assemblage_dataset_cli/dataset_utils.py db_construct expects:
        - "URL", "Platform", "Build_mode", "Optimization", "Commit", "Pushed_at"
        - "Binary_info_list" is merged in by the caller when DWARF info is available
        """
        metadata = {
            "Platform": self.platform,
            "Build_mode": self.build_strategy.build_mode
            if hasattr(self.build_strategy, "build_mode")
            else "RelWithDebInfo",
            "Compiler": self.build_strategy.compiler,
            "Compiler_version": self.build_strategy.compiler_version,
            "URL": task.url,
            "Commit": commit_hexsha,
            "Optimization": compiler_flag,
            "Pushed_at": getattr(task, "updated_at", ""),
            "compiler_flag": compiler_flag,
            "language": self.build_strategy.language,
            "library": self.library,
        }
        return metadata

    def save_metadata_locally(
        self, clone_dir: str, commit_hexsha: str, compiler_flag: str, metadata: dict
    ) -> str:
        """
        Save metadata JSON file locally in the clone directory
        Returns the path to the metadata file
        """
        metadata_filename = "assemblage_meta.json"
        metadata_path = os.path.join(clone_dir, metadata_filename)

        try:
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2)
            logger.info(f"Metadata saved to {metadata_path}")
            return metadata_path
        except Exception as e:
            logger.warning(f"Failed to save metadata to {metadata_path}: {e}")
            return None

    @staticmethod
    def _artifact_prefix(username, project_name, commit_hexsha, compiler, compiler_flag):
        """Build the flat S3 prefix: username_project_commithash_compiler_opt/"""
        return f"{username}_{project_name}_{commit_hexsha}_{compiler}_{compiler_flag}"

    def save_metadata_to_s3(
        self,
        clone_dir: str,
        username: str,
        project_name: str,
        commit_hexsha: str,
        compiler: str,
        compiler_flag: str,
        metadata: dict,
    ) -> bool:
        """
        Upload metadata JSON file to S3
        """
        metadata_filename = "assemblage_meta.json"
        metadata_path = os.path.join(clone_dir, metadata_filename)
        prefix = self._artifact_prefix(
            username, project_name, commit_hexsha, compiler, compiler_flag
        )

        try:
            with open(metadata_path, "w") as f:
                json.dump(metadata, f, indent=2)

            s3_key = f"{prefix}/{metadata_filename}"
            if self.ArtifactBucket.upload_file(metadata_path, s3_key):
                logger.info(f"Metadata uploaded to S3: {s3_key}")
                try:
                    os.remove(metadata_path)
                except Exception as e:
                    logger.warning(f"Failed to clean up metadata file {metadata_path}: {e}")
                return True
            else:
                logger.warning(f"Failed to upload metadata to S3: {s3_key}")
                return False
        except Exception as e:
            logger.warning(f"Failed to save/upload metadata: {e}")
            return False

    def save_project_to_s3(
        self, clone_dir: str, username: str, project_name: str, commit_hexsha: str
    ):
        """
        ZIP and save projects to s3 Project-Archive/<github_username>/<github_project>/commit.zip
        """

        try:
            archive = shutil.make_archive(
                f"{TEMP_DIR}/{commit_hexsha}", "gztar", clone_dir
            )  # zip up
            s3_key = f"{username}/{project_name}/{commit_hexsha}.tar.gz"

            self.ProjectBucket.upload_file(archive, s3_key)
            try:
                os.remove(archive)
            except Exception:
                # dont want to fail if successfully failed, just hasnt tidied up
                logger.warning(f"Failed to delete archive: {s3_key} after uploading to s3")

            return True
        except Exception as e:
            logger.warning(
                f"failed to save {clone_dir} as zip archive to {self.ProjectBucket}/{username}/{project_name}/{commit_hexsha}.tar.gz : {e}"
            )
            return False

    def _read_latest_commit_pointer(self, username: str, project_name: str):
        """Read the latest.txt pointer from project-archive to get the commit hash."""
        pointer_key = f"{username}/{project_name}/latest.txt"
        local_path = f"{TEMP_DIR}/{username}_{project_name}_latest.txt"
        try:
            if self.ProjectBucket.download_file(pointer_key, local_path):
                with open(local_path) as f:
                    commit = f.read().strip()
                os.remove(local_path)
                if commit:
                    logger.info(f"Found cached commit {commit} for {username}/{project_name}")
                    return commit
        except Exception:
            pass
        return None

    def _try_download_project(self, username: str, project_name: str, commit_hexsha: str):
        """Try to download a project archive from S3. Returns local path or None."""
        s3_key = f"{username}/{project_name}/{commit_hexsha}.tar.gz"
        local_path = f"{TEMP_DIR}/{username}_{project_name}_{commit_hexsha}.tar.gz"
        try:
            if self.ProjectBucket.object_exists(s3_key):
                if self.ProjectBucket.download_file(s3_key, local_path):
                    logger.info(f"Downloaded project archive from S3: {s3_key}")
                    return local_path
        except Exception as e:
            logger.debug(f"Failed to download project archive: {e}")
        return None

    def save_binaries(self, target_dir, task, original_files, commit_hexsha, compiler_flag: str):
        """Store binaries locally or on S3, and notify coordinator."""
        logger.info(f"Saving binaries of Repo: {task.url}")

        self.build_strategy.own_dir(os.path.dirname(target_dir))

        bin_found = {
            f
            for f in self.build_strategy.find_binaries(target_dir)
            if os.path.exists(f) and f not in original_files
        }
        if not bin_found:
            logger.warning("No binaries found, build may have failed")
            return target_dir, False

        logger.info(f"{len(bin_found)} binaries found")
        username, project = target_dir.rstrip("/").split("/")[-2:]
        compiler = self.build_strategy.compiler
        prefix = self._artifact_prefix(username, project, commit_hexsha, compiler, compiler_flag)

        if self.s3_client:
            dest_base_full = f"{self.ArtifactBucket}/{prefix}"
        else:
            dest_base_full = os.path.join(BINPATH, "successes", prefix)
            os.makedirs(dest_base_full, exist_ok=True)
        all_saved = True
        for fpath in bin_found:
            base_name = os.path.basename(fpath)
            if self.platform == "windows":
                base_name = self._process_window_file_names(fpath)

            if self.s3_client:
                s3_key = f"{prefix}/{base_name}"
                if self.ArtifactBucket.upload_file(fpath, s3_key):
                    logger.info(f"Uploaded {fpath} -> {s3_key}")
                else:
                    all_saved = False
                    logger.warning(f"Failed to upload {fpath} -> {s3_key}")
            else:
                dest_file = os.path.join(dest_base_full, base_name)
                shutil.copy2(fpath, dest_file)
                if self.platform == "linux":
                    try:
                        os.chmod(dest_file, NON_EXE_MODE)
                    except Exception:
                        logger.warning(f"Failed to change permissions on {dest_file}")
                        all_saved = False

            self.process_send_msg(
                kind=InputQueue.BINARY, task=task, file_name=fpath if self.s3_client else dest_file
            )

        if not self.s3_client:
            self.build_strategy.own_dir(dest_base_full)
        return dest_base_full, all_saved

    def process_send_msg(self, kind: InputQueue, task, **kwarg):
        """
        send message to the coordinator input queue
        Remember input is from the perspective of the coordinator so input == output in builder and output == input
        """
        ret = {}
        queue = MQQueue(kind)
        routing_key = None
        # temporary, to make it more clear that these two messages are logically distinct
        if kind == InputQueue.BUILD_REG:
            ret = BuilderRegIn(
                name=self.name,
                uuid=self.uuid,
                compiler=self.build_strategy.compiler,
                library=self.library,
                language=self.build_strategy.language,
                platform=self.platform,
                compiler_flag=self.compiler_flag or "",
                build_command=self.build_command or "",
                build_system=self.build_system,
            ).to_json()
            ctrl_conn: Connection | None = self.mq_client.get_connection(
                f"{self}-{OutputQueue.BUILDER_CTRL}"
            )
            logger.info(f"Reply to {self.control_queue_in.name}. corr_id {self.uuid}")
            if ctrl_conn:
                logger.info(f"Registering builder with {ret}")
                ctrl_conn.send_msg(
                    queue=queue,
                    msg=ret,
                    #    exchange='builder.register',
                    reply_to=f"{self.control_queue_in.name}",
                    corr_id=self.uuid,
                )
                return
            else:
                # do we want to create if does not exist then send message?
                raise Exception(f"Connection {self}-ctrl does not exist")
        else:
            msg = None
            match kind:
                case InputQueue.CLONE:
                    msg = CloneStatusMsgIn(
                        url=kwarg["url"],
                        opt_id=self.opt_id,
                        status=kwarg["status"],
                        msg=kwarg["msg"][-1000:],
                        task_id=task.task_id,
                    )
                case InputQueue.BUILD:
                    msg = BuildStatusMsgIn(
                        url=kwarg["url"],
                        opt_id=self.opt_id,
                        status=kwarg["status"],
                        msg=kwarg["msg"][-1000:],
                        task_id=task.task_id,
                        build_time=kwarg["build_time"],
                        commit_hexsha=kwarg["commit_hexsha"],
                    )
                case InputQueue.BINARY:
                    msg = BinaryTaskMsgIn(
                        task_id=task.task_id,
                        file_name=kwarg["file_name"],
                    )
                case InputQueue.POST_ANALYSIS:
                    msg = PostAnalysisTaskMsgIn(
                        file_name=kwarg["file_name"], platform=self.platform
                    )
                    routing_key = f"post_analysis.{self.opt_id}"

                    # it's important to redefine the queue in order to include proper routing key
                    # TODO: test and troubleshoot, the first 2 params were chosen arbitrarily
                    queue = MQQueue(
                        name=routing_key, exchange_name=routing_key, routing_key=routing_key
                    )
                    # self.mq_client.send_kind_msg(f"post_analysis.{self.opt_id}", json.dumps(ret))
                case _:
                    logger.warning("Unknown type of message %s, not sending... ", kind)
                    return

            job_conn = self.mq_client.get_connection(f"{self}")
            assert isinstance(msg, MQMsg)
            if job_conn:
                job_conn.send_msg(queue, msg.to_json())
            else:
                raise Exception("No connection for job handler exists")
