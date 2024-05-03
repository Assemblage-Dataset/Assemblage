import os
import glob
import random
import string
from tqdm import tqdm
import json
import random
import string
import hashlib
import json
import os
from subprocess import Popen, PIPE, STDOUT, TimeoutExpired
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading
import math
from db import Dataset_DB
from dataset_orm import *

TIMEOUT = 15


def get_md5(s):
    return hashlib.md5(s.encode()).hexdigest()


def assign_path(filename):
    md5 = get_md5(filename)
    return f"{md5[:2]}/{md5[2:]}"


def runcmd(cmd):
    stdout, stderr = None, None
    if os.name != 'nt':
        cmd = "exec " + cmd
    with Popen(cmd, shell=True, stdin=PIPE, stdout=PIPE, stderr=STDOUT, close_fds=True) as process:
        try:
            stdout, stderr = process.communicate(timeout=TIMEOUT)
        except TimeoutExpired:
            if os.name == 'nt':
                Popen("TASKKILL /F /PID {pid} /T".format(pid=process.pid))
            else:
                process.kill()
                exit()
    return stdout, stderr, process.returncode


def process(zip_path, dest):
    data_prefix = dest
    runcmd(f"mkdir {data_prefix}")
    tmp = f"{data_prefix}/tmp"
    folder_prefix = f"{data_prefix}/bins"
    runcmd(f"rm -rf {folder_prefix}")
    runcmd(f"mkdir {folder_prefix}")
    jsonfolders = f"{data_prefix}/jsons"
    runcmd(f"rm -rf {jsonfolders}")
    runcmd(f"mkdir {jsonfolders}")
    print("Collecting binary files")
    zipped_files = [x for x in glob.glob(f"{zip_path}/*") if os.path.isfile(x)]
    total = len(zipped_files)
    print(f"Found {total} zips")
    totalbin = 0
    pdb_rela = {}
    for i, f in enumerate(zipped_files):
        bin_found, pdb, bins = unzip_process(
            f, zip_path, folder_prefix, tmp, jsonfolders, data_prefix)
        totalbin += bin_found
        pdb_rela[pdb] = bins
        print(
            f"Task {f}, {i}/{total}, found {bin_found}, total {totalbin} found")
    with open(f"{dest}/pdb_rela.json", "w") as f:
        json.dump(pdb_rela, f)


def unzip_process(f, zip_path, folder_prefix, tmp, jsonfolders, data_prefix):
    runcmd(f"rm -rf {tmp}")
    runcmd(f"mkdir {tmp}")
    runcmd(f"unzip {f} -d {tmp}")
    bin_found = []
    identifier = os.urandom(16).hex()
    if os.path.isfile(os.path.join(tmp, "pdbinfo.json")):
        with open(os.path.join(tmp, "pdbinfo.json")) as pdbf:
            pdb = json.load(pdbf)
        for binf in glob.glob(tmp+"/*.exe")+glob.glob(tmp+"/*.dll"):
            bin_name = binf.split("/")[-1]
            plat = pdb["Platform"] or "unknown"
            mode = pdb["Build_mode"]
            toolv = pdb["Toolset_version"]
            opti = pdb["Optimization"]
            github_url = pdb["URL"]
            identifier = get_md5(github_url)+f"_{plat}_{mode}_{toolv}_{opti}"
            dest_path = f"{folder_prefix}/{identifier}_{bin_name}"
            if os.path.isfile(dest_path):
                print(dest_path, 'existed')
                continue
            bin_found.append(dest_path)
            runcmd(f"cp '{binf}' {dest_path}")
    else:
        return 0, "", []
    pdbpath = os.path.join(tmp, "pdbinfo.json")
    pdb_dest = os.path.join(jsonfolders, f"{identifier}.json")
    runcmd(f"cp {pdbpath} {pdb_dest}")
    runcmd(f"rm -rf {tmp}")
    return len(bin_found), pdb_dest, bin_found


def filter_size(size_upper, size_lower, file_limit, binpath, dest_path):
    binpath = binpath+"/bins"
    print("Filtering files")
    if not file_limit:
        file_limit = math.inf
    if not size_lower:
        size_lower = 0
    if not size_upper:
        size_upper = math.inf
    for f in tqdm(os.listdir(binpath)):
        bts = os.path.getsize(os.path.join(binpath, f))
        kb = bts/1024
        if kb >= size_lower and kb <= size_upper:
            runcmd(
                f"cp {os.path.join(binpath, f)} {os.path.join(dest_path, f)}")
            file_limit -= 1
        if not file_limit:
            break
    print(f"Copying files")
    for f in tqdm(os.listdir(dest_path)):
        urlmd5 = f.split("_")[0]
        runcmd(f"cp {binpath.replace('/bins','')}/jsons/{urlmd5}* {dest_path}")
    print("Copying pdb files")
    for f in tqdm(os.listdir(dest_path)):
        if f.endswith("json") and not f.endswith("pdb_rela.json"):
            with open(os.path.join(dest_path, f)) as fhandler:
                pdb = json.load(fhandler)
            plat = pdb["Platform"]
            mode = pdb["Build_mode"]
            toolv = pdb["Toolset_version"]
            md5 = get_md5(pdb["URL"])
            opti = pdb["Optimization"]
            bin_prefix = f"{md5}_{plat}_{mode}_{toolv}_{opti}"
            try:
                os.makedirs(os.path.join(dest_path, bin_prefix))
            except:
                pass
            for x in os.listdir(dest_path):
                if x.startswith(bin_prefix) and (x.endswith("exe") or x.endswith("dll")):
                    runcmd(
                        f"mv {dest_path}/{x} {os.path.join(dest_path, bin_prefix)}/{x}")
            runcmd(f"mv {dest_path}/{f} {os.path.join(dest_path, bin_prefix)}")
    for folder in os.listdir(dest_path):
        if os.path.isdir(f"{dest_path}/{folder}"):
            files = os.listdir(f"{dest_path}/{folder}")
            if len(files) < 2:
                runcmd(f"rm -r {dest_path}/{folder}")
        else:
            runcmd(f"rm {dest_path}/{folder}")


def db_construct(dbfile, target_dir):
    print("Creating database")
    try:
        os.remove(dbfile)
    except:
        pass
    init_clean_database(f"sqlite:///{dbfile}")
    db = Dataset_DB(f"sqlite:///{dbfile}")
    print("Constructing database, this will take a while")
    binaries = []
    functions = []
    lines = []
    function_id = 1
    bin_id = 1
    dropped_bin = 0
    for folder in tqdm(os.listdir(target_dir)):
        identifier = folder
        bins = [x for x in os.listdir(os.path.join(
            target_dir, folder)) if not x.endswith(".json")]
        pdbinfo = json.load(
            open(os.path.join(target_dir, identifier, f"{identifier}.json")))
        binary_rela = {}
        for binfile in bins:
            filename = binfile.replace(identifier+"_", "")
            path = f"{assign_path(binfile)}"
            path = "".join([x for x in path if (x in string.printable and x)])
            try:
                os.makedirs(f"{target_dir}/{path}")
            except:
                pass
            file_name_clean = "".join(
                [x for x in binfile if (x in string.printable and x)])
            runcmd(
                f"mv {target_dir}/{folder}/{binfile} {target_dir}/{path}/{file_name_clean}")
            binaries.append({
                "id": bin_id,
                "path": os.path.join(target_dir, path, file_name_clean),
                "file_name": filename,
                "platform": pdbinfo["Platform"],
                "build_mode": pdbinfo["Build_mode"],
                "toolset_version": pdbinfo["Toolset_version"],
                "pushed_at": datetime.datetime.strptime(pdbinfo["Pushed_at"], '%m/%d/%Y, %H:%M:%S'),
                "optimization": pdbinfo["Optimization"],
                "github_url": pdbinfo["URL"],
                "size": os.path.getsize(os.path.join(target_dir, path, file_name_clean))//1024
            })
            binary_rela[filename] = bin_id
            bin_id += 1
            for binary_file_info in pdbinfo["Binary_info_list"]:
                if filename == binary_file_info["file"].replace("\\", "/").split("/")[-1]:
                    bin_id_found = binary_rela[filename]
                    for binary in binaries:
                        if binary["id"] == bin_id_found:
                            binary["file_name"] = binary_file_info["file"].replace(
                                "\\", "/").split("/")[-1]
                    if len(binary_file_info["functions"]) == 0:
                        for x in binaries:
                            if x["id"] == bin_id_found:
                                binaries.remove(x)
                                print(x)
                        dropped_bin += 1
                    else:
                        for function_info in binary_file_info["functions"]:
                            function_name = function_info["function_name"]
                            intersect_ratio = float(
                                function_info["intersect_ratio"].replace("%", ""))/100
                            source_file = function_info["source_file"]
                            rva_strings = ",".join(
                                [f"{x['rva_start']}-{x['rva_end']}" for x in function_info["function_info"]])
                            functions.append({
                                "id": function_id,
                                "name": function_name,
                                "source_file": source_file,
                                "intersect_ratio": intersect_ratio,
                                "rvas": rva_strings,
                                "binary_id": bin_id_found
                            })
                            if bin_id_found not in [x["id"] for x in binaries]:
                                print("ERR", bin_id_found)
                                print("ERR", len(
                                    binary_file_info["functions"]))
                                exit()
                            source_file = ""
                            for line_info in function_info["lines"]:
                                line_number = line_info["line_number"]
                                rva_addr = line_info["rva"]
                                length = line_info["length"]
                                source_code = line_info["source_code"]
                                if "source_file" in line_info:
                                    source_file = line_info["source_file"]
                                lines.append({
                                    "line_number": line_number,
                                    "rva": rva_addr,
                                    "length": length,
                                    "source_code": source_code,
                                    "function_id": function_id
                                })
                            function_id += 1
        runcmd(f"rm -rf {target_dir}/{folder}")
    print("Adding to database")
    print(f"Adding binaries total: {len(binaries)}, dropped: {dropped_bin}")
    db.add_binaries(binaries)
    print(f"Adding functions total: {len(functions)}")
    db.add_functions(functions)
    print(f"Adding lines total: {len(lines)}")
    db.add_lines(lines)
    print("Done")
