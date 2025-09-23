'''
disasm.py should be able to run different disassemblers and post result to s3

Now on going:
-  objdump
-  ddisasm
-  ghidra   https://github.com/radareorg/r2ghidra

'''

from enum import Enum
import json
import logging
import os
import shutil
import time
import os
import zipfile
import json
import hashlib

from tqdm import tqdm

import boto3

from assemblage.worker.profile import AWSProfile
from assemblage.worker.base_worker import BasicWorker
from assemblage.worker.build_method import post_processing_pdb, post_processing_s3, cmd_with_output


TMP_PATH = "tmp"


class AvailableDisassembler(Enum):
    DDISASM = 0
    OBJDUMP = 1
    GHIDRA = 2
    DIA2DUMP = 3


def get_md5(s):
    return hashlib.md5(s.encode()).hexdigest()


class Disassembler:
    """ runner class for different disassembler """

    def __init__(self, disassembler_type: AvailableDisassembler, ) -> None:
        self.disassembler_type = disassembler_type
        if not self.check_disassembler_available():
            logging.error("Target disassembler not installed!")
            exit(0)

    def disasm(self, binary_path, outdir):
        """
        run disassembler
        binary_path:    input binary, should be PE/executable/so
        outdir:         zipped result, contain all necessary info to reassemble it
                        output zip convention is `<binary_name>.asm.zip`
        return:         disassemble success or not, if success, return generated zip
                        file name
        """
        outdir = os.path.realpath(outdir)
        if not self.validate_file(binary_path):
            return False
        fname = os.path.basename(binary_path)
        asm_tmp_dir = os.path.join(outdir, f"{fname}_tmp")
        asm_res_success_flag = True
        try:
            os.makedirs(asm_tmp_dir)
        except:
            shutil.rmtree(asm_tmp_dir)
            os.makedirs(asm_tmp_dir)
        if self.disassembler_type == AvailableDisassembler.DDISASM:
            asm_res_success_flag = self.run_ddisasm(binary_path, asm_tmp_dir)
        elif self.disassembler_type == AvailableDisassembler.OBJDUMP:
            asm_res_success_flag = self.run_objdump(binary_path, asm_tmp_dir)
        else:
            logging.error("disassembler not implemented")
            return False
        if not asm_res_success_flag:
            return False
        zip_name = os.path.join(outdir, f"{fname}_disasm")
        self.__zip_file(asm_tmp_dir, zip_name)
        return zip_name+".zip"

    def check_disassembler_available(self):
        """ check if it disasm command is available """
        if self.disassembler_type == AvailableDisassembler.DDISASM:
            return shutil.which("ddisasm") is not None
        elif self.disassembler_type == AvailableDisassembler.OBJDUMP:
            return shutil.which("objdump") is not None
        else:
            logging.error("disassembler not implemented")
            return False

    def validate_file(self, binary_path):
        """ check if a file is valid input of disassembler """
        if binary_path.endswith(".exe") or binary_path.endswith(".dll"):
            return True
        return False

    def __zip_file(self, input_path, output_zipfile):
        shutil.make_archive(output_zipfile, "zip", input_path)

    def run_objdump(self, binary_path, outdir):
        logging.info("running objdump on %s",  binary_path)
        cmd = f"cd {outdir} && objdump -D {binary_path} > {binary_path}.asm"
        # logging.info(cmd)
        out, err, exit_code = cmd_with_output(cmd)
        # logging.info(out)
        return True

    def run_ddisasm(self, binary_path, outdir):
        outdir = os.path.realpath(outdir)
        logging.info("Running ddisasm on %s",  binary_path)
        fname = os.path.basename(binary_path)
        cmd = f"cd {outdir} && ddisasm --asm={os.path.join(outdir, fname+'.asm')} --generate-import-libs {os.path.realpath(binary_path)}"
        out, err, exit_code = cmd_with_output(cmd)
        # if exit_code != 0:
        #    logging.error("ddisasm running failed with out >> %s \n err >> %s ", out, err)
        #    return False
        logging.info("Ddisasm done on %s",  binary_path)
        return True

    def run_r2ghidra(self, binary_path, outdir):
        # TODO: need impl
        pass


class DDisasmWorker(BasicWorker):
    """
    a ddisasm worker
    """

    def __init__(self, rabbitmq_host, rabbitmq_port, rpc_stub,
                 worker_type, opt_id, send_binary_method,
                 s3_bucket_name="assemblage-data/"):
        super().__init__(rabbitmq_host, rabbitmq_port, rpc_stub, worker_type, opt_id)
        # run a ftp server on 10086
        time.sleep(5)
        if not os.path.exists("/binaries/ftp"):
            os.makedirs("/binaries/ftp")
        # ftp_server = AssemblageFtpSever("/binaries/ftp")
        # ftp_thread = threading.Thread(target=ftp_server.start, daemon=True)
        # ftp_thread.start()
        self.sesh = boto3.session.Session(profile_name='assemblage')
        self.s3 = self.sesh.client('s3')
        self.s3_bucket_name = s3_bucket_name

    def on_init(self):
        logging.info("ddisasm worker starting ....")

    def setup_job_queue_info(self):
        self.input_queue_name = 'post_analysis'
        self.input_queue_args = {
            "durable": True
        }

    def download_from_s3(self, s3_path, dest_path):
        logging.info("s3_path %s", s3_path)
        try:
            with open(dest_path, 'wb') as f:
                self.s3.download_fileobj(self.s3_bucket_name, s3_path, f)
            logging.info("S3 bucket downloaded")
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
        if "linux" in repo['file_name']:
            return
        self.download_from_s3("platform/windows/"+repo['file_name'].split("/")[-1],
                              repo['file_name'].split("/")[-1])
        tmp_bin_dir = repo['file_name'].replace("data/", "")+"_folder"
        tmp_zip_dir = repo['file_name'].replace("data/", "")+"_zip"
        try:
            os.makedirs(tmp_bin_dir)
        except:
            os.remove(tmp_bin_dir)
            os.makedirs(tmp_bin_dir)
        zipfile = repo['file_name'].replace("data/", "")
        cmd_with_output(f"unzip {zipfile} -d {tmp_bin_dir}")
        for f in os.listdir(tmp_bin_dir):
            disassembler = Disassembler(AvailableDisassembler.DDISASM)
            res_zip_file = disassembler.disasm(os.path.join(tmp_bin_dir, f),
                                               tmp_zip_dir)
            if res_zip_file:
                self.upload_to_s3(res_zip_file,
                                  "postprocessed/" +
                                  zipfile.replace("/", "_") +
                                  os.path.basename(res_zip_file))
        cmd_with_output(f"rm -rf {tmp_zip_dir}")
        cmd_with_output(f"rm -rf {tmp_bin_dir}")
        cmd_with_output(f"rm -rf {zipfile}")


class Dia2dumpProcessor(BasicWorker):

    def __init__(self, rabbitmq_host, rabbitmq_port, rpc_stub,
                 worker_type, opt_id, aws_profile: AWSProfile):
        super().__init__(rabbitmq_host, rabbitmq_port, rpc_stub, worker_type, opt_id)
        self.sesh = boto3.session.Session(profile_name='assemblage')
        self.s3 = self.sesh.client('s3')
        self.aws_profile = aws_profile
        try:
            os.makedirs(TMP_PATH)
        except:
            pass

    def on_init(self):
        logging.info("Dia2DumpProcessor worker starting ....")

    def setup_job_queue_info(self):
        self.input_queue_name = 'post_analysis'
        self.input_queue_args = {
            "durable": True
        }

    def download_from_s3(self, s3_path, dest_path):
        logging.info("s3_path %s", s3_path)
        try:
            with open(dest_path, 'wb') as f:
                self.s3.download_fileobj(self.aws_profile.s3_bucket_name, s3_path, f)
            logging.info("S3 bucket downloaded")
        except Exception as err:
            logging.error(err)

    def upload_to_s3(self, target_path, s3_path):
        try:
            with open(target_path, "rb") as fh:
                self.s3.upload_fileobj(fh, self.aws_profile.s3_bucket_name, s3_path)
            logging.info(f'Uploaded %s %s %s', target_path,
                         self.s3_bucket_name, s3_path)
        except Exception as e:
            logging.error(e)

    def job_handler(self, ch, method, _props, body):
        repo = json.loads(body)
        ch.basic_ack(method.delivery_tag)
        if "linux" in repo['file_name']:
            return
        onezipfile = repo['file_name'].split("/")[-1]
        logging.info("onezipfile %s", onezipfile)
        onezipfile_clean = onezipfile.replace(".", "_")+"extracted"
        path_to_zip_file = os.path.join(
            TMP_PATH, repo['file_name'].split("/")[-1])
        self.download_from_s3("platform/windows/"+repo['file_name'].split("/")[-1],
                              path_to_zip_file)
        try:
            with zipfile.ZipFile(path_to_zip_file, 'r') as zip_ref:
                zip_ref.extractall(os.path.join(TMP_PATH, onezipfile_clean))
        except:
            pass
        if not os.path.isfile(os.path.join(TMP_PATH, onezipfile_clean, "pdbinfo.json")):
            logging.info(cmd_with_output(
                f"aws s3 rm s3://assemblage-data/platform/windows/{onezipfile}"))
            return
        try:
            with open(os.path.join(TMP_PATH, onezipfile_clean, "pdbinfo.json"), "r") as f:
                pdb = json.load(f)
        except:
            logging.info(cmd_with_output(
                f"aws s3 rm s3://assemblage-data/platform/windows/{onezipfile}"))
            return
        os.remove(os.path.join(TMP_PATH, onezipfile_clean, "pdbinfo.json"))
        os.remove(path_to_zip_file)
        Platform = pdb["Platform"]
        Build_mode = pdb["Build_mode"]
        Toolset_version = pdb["Toolset_version"]
        URL = pdb["URL"]
        Optimization = pdb["Optimization"]
        Pushed_at = pdb["Pushed_at"]
        post_processing_pdb(os.path.join(TMP_PATH, onezipfile_clean),
                            Build_mode,
                            Platform,
                            {"url": URL, "updated_at": Pushed_at},
                            Toolset_version,
                            Optimization)
        newfilename = get_md5(
            URL)+f"_{Platform}_{Build_mode}_{Toolset_version}_{Optimization}"
        cmd = f"cd {os.path.join(TMP_PATH, onezipfile_clean)}&&7z a -r -tzip {newfilename} *"
        out, _err, _exit_code = cmd_with_output(cmd, platform='windows')
        logging.info(cmd_with_output(
            f"aws s3 rm s3://assemblage-data/platform/windows/{onezipfile}"))
        post_processing_s3("platform/windows/" + f"{newfilename}.zip", os.path.join(
            TMP_PATH, onezipfile_clean, newfilename+".zip"), self.aws_profile)
        cmd_with_output(
            f"del /f/q/s {os.path.join(TMP_PATH, onezipfile_clean)}")
        cmd_with_output(f"rmdir {os.path.join(TMP_PATH, onezipfile_clean)}")
