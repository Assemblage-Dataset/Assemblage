from assemblage.worker.build_method import post_processing_pdb, post_processing_s3
from assemblage.worker.build_method import cmd_with_output
import os
import zipfile
import json
from tqdm import tqdm
import logging
import hashlib

zips_folder = ""
dest_folder = ""


def get_md5(s):
    return hashlib.md5(s.encode()).hexdigest()


for onezipfile in tqdm(os.listdir(zips_folder)):
    path_to_zip_file = os.path.join(zips_folder, onezipfile)
    try:
        with zipfile.ZipFile(path_to_zip_file, 'r') as zip_ref:
            zip_ref.extractall(os.path.join(dest_folder, onezipfile))
        if os.path.isfile(os.path.join(dest_folder, onezipfile, "pdbinfo.json")):
            with open(os.path.join(dest_folder, onezipfile, "pdbinfo.json"), "r") as f:
                pdb = json.load(f)
            os.remove(os.path.join(dest_folder, onezipfile, "pdbinfo.json"))
            Platform = pdb["Platform"]
            Build_mode = pdb["Build_mode"]
            Toolset_version = pdb["Toolset_version"]
            URL = pdb["URL"]
            Optimization = pdb["Optimization"]
            Pushed_at = pdb["Pushed_at"]
            newfilename = get_md5(
                URL)+f"_{Platform}_{Build_mode}_{Toolset_version}_{Optimization}"
            post_processing_pdb(os.path.join(dest_folder, onezipfile),
                                Build_mode,
                                Platform,
                                {"url": URL, "updated_at": Pushed_at},
                                Toolset_version,
                                Optimization)
            cmd = f"cd {os.path.join(dest_folder, onezipfile)}&&7z a -r -tzip {newfilename}"
            out, _err, _exit_code = cmd_with_output(cmd, platform='windows')
            post_processing_s3("platform/windows/" + f"{newfilename}.zip", os.path.join(
                dest_folder, onezipfile, f"{newfilename}.zip"))
            cmd_with_output(
                f"del /f/q/s {os.path.join(dest_folder, onezipfile)}")
    except:
        pass
    logging.info(cmd_with_output(
        f"aws s3 rm s3://assemblage-data/platform/windows/{onezipfile}"))
    os.remove(path_to_zip_file)
