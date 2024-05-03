"""
A General Post Processor

"""

from abc import ABC, abstractclassmethod
import json
import logging
import os
import shutil
import time

import boto3

from assemblage.worker.base_worker import BasicWorker
from assemblage.worker.profile import AWSProfile
from assemblage.worker.build_method import cmd_with_output
from assemblage.worker.find_bin import find_elf_bin


class PostAnalysis(ABC):
    """ the base class for all kind of post analysis """

    def __init__(self, name) -> None:
        self.name = name

    @abstractclassmethod
    def analysis(self, bin_file, analysis_out_dir):
        """
        `bin_file` is file path of binary need analysis, write result to `analysis_out_dir`
        it will be compressed and then posted into AWS after this function returns
        """


class PostProcessor(BasicWorker):

    def __init__(self, rabbitmq_host, rabbitmq_port, rpc_stub, 
                 worker_type, opt_id, aws_profile: AWSProfile,
                 analysis: PostAnalysis):
        super().__init__(rabbitmq_host, rabbitmq_port, rpc_stub, worker_type, opt_id)
        # run a ftp server on 10086
        time.sleep(5)
        if not os.path.exists("/binaries/ftp"):
            os.makedirs("/binaries/ftp")
        # ftp_server = AssemblageFtpSever("/binaries/ftp")
        # ftp_thread = threading.Thread(target=ftp_server.start, daemon=True)
        # ftp_thread.start()
        self.sesh = boto3.session.Session(profile_name=aws_profile.profile_name)
        self.s3 = self.sesh.client('s3')
        self.s3_bucket_name = aws_profile.s3_bucket_name
        self.analysis = analysis

    def on_init(self):
        logging.info("postprocess worker %s starting ....", self.worker_type)

    def setup_job_queue_info(self):
        # self.input_queue_name = 'post_analysis'
        self.input_queue_name = f'post_analysis.{self.opt_id}'
        self.input_queue_args = {
            "durable": True
        }

    def __zip_file(self, input_path, output_zipfile):
        return shutil.make_archive(output_zipfile, "zip", input_path)

    def download_from_s3(self, s3_path, dest_path):
        logging.info("s3_path %s", s3_path)
        try:
            with open(dest_path, 'wb') as f:
                self.s3.download_fileobj(self.s3_bucket_name, s3_path, f)
            logging.info("S3 bucket downloaded to %s", dest_path)
        except Exception as err:
            logging.error(err)

    def upload_to_s3(self, target_path, s3_path):
        try:
            with open(target_path, "rb") as fh:
                self.s3.upload_fileobj(fh, self.s3_bucket_name, s3_path)
            logging.info(f'Uploaded %s %s %s', target_path,
                         self.s3_bucket_name, s3_path)
        except Exception as e:
            logging.error(e)

    def job_handler(self, ch, method, _props, body):
        repo = json.loads(body)
        ch.basic_ack(method.delivery_tag)
        logging.info(f" >>>>>>>>   {body}")
        # if "linux" in repo['file_name']:
        #     return
        zipfile = repo['file_name'].split("/")[-1]
        self.download_from_s3(repo['file_name'], zipfile)
        tmp_bin_dir = zipfile+"_folder"
        tmp_zip_dir = zipfile+"_zip"
        try:
            os.makedirs(tmp_bin_dir)
        except:
            os.remove(tmp_bin_dir)
            os.makedirs(tmp_bin_dir)
        try:
            os.makedirs(tmp_zip_dir)
        except:
            os.remove(tmp_zip_dir)
            os.makedirs(tmp_zip_dir)
        # zipfile = repo['file_name'].replace("data/", "")
        # logging.info(f" >>>>>>>>  here>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>")
        out, err, exit_code = cmd_with_output(f"unzip {zipfile} -d {tmp_bin_dir}")
        logging.info("Unziped: %s ,,, %s", out, err)
        tmp_zip_dir = os.path.realpath(tmp_zip_dir)
        res_zip_file = f"/{os.path.basename(zipfile)}_{self.analysis.name}"
        bin_file_paths = find_elf_bin(tmp_bin_dir)
        for fname in bin_file_paths:
            binary_path =os.path.join(tmp_bin_dir, fname)
            self.analysis.analysis(binary_path, tmp_zip_dir)
        res_zip_file = self.__zip_file(tmp_zip_dir, res_zip_file)
        logging
        if res_zip_file:
            self.upload_to_s3(res_zip_file,
                                "postprocessed/" +
                                zipfile.replace("/", "_") +
                                os.path.basename(res_zip_file))
        cmd_with_output(f"rm -rf {tmp_zip_dir}")
        cmd_with_output(f"rm -rf {tmp_bin_dir}")
        cmd_with_output(f"rm -rf {zipfile}")
        logging.info(f"Uploading finish {body}")



    
