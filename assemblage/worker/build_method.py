"""
Decide build method based on files in repo directory

Assemblage Windows Worker Build Methods
1. Modify XML file
2. Build with msbuild
Chang
Yihao
"""

import os
import glob
import re
import logging
import subprocess
import signal
import json
import string
import boto3
import hashlib
import shutil

from botocore.exceptions import ClientError
from assemblage.consts import BuildStatus, PDBJSONNAME, BINPATH
from assemblage.windows.parsers.proj import Project
from assemblage.windows.parsers.sln import Solution
from assemblage.analyze.analyze import get_build_system
from assemblage.worker.ctagswrap import get_functions
from pathlib import PureWindowsPath, PurePosixPath

logging.basicConfig(level=logging.INFO)

def sha256sum(filename):
    h  = hashlib.sha256()
    b  = bytearray(128*1024)
    mv = memoryview(b)
    with open(filename, 'rb', buffering=0) as f:
        while n := f.readinto(mv):
            h.update(mv[:n])
    return h.hexdigest()

def filter_ascii(s):
    return ''.join(filter(lambda x: x in string.printable, s))

def cmd_with_output(cmd, timelimit=60, platform='linux', cwd=''):
    """ The cmd execution function """
    if isinstance(cmd, list):
        cmd = " ".join(cmd)
    if not cwd:
        with subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            close_fds=True,
                            shell=True) as process:
            try:
                out, err = process.communicate(timeout=timelimit)
                exit_code = process.wait()
                process.kill()
                return out, err, exit_code
            except subprocess.TimeoutExpired:
                if platform == 'linux':
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                return b"subprocess.TimeoutExpired", b"subprocess.TimeoutExpired", 1
    else:
        with subprocess.Popen(cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE,
                            close_fds=True,
                            shell=True,
                            cwd=cwd) as process:
            try:
                out, err = process.communicate(timeout=timelimit)
                exit_code = process.wait()
                process.kill()
                return out, err, exit_code
            except subprocess.TimeoutExpired:
                if platform == 'linux':
                    os.killpg(os.getpgid(process.pid), signal.SIGTERM)
                return b"subprocess.TimeoutExpired", b"subprocess.TimeoutExpired", 1


def dia_get_func_funcinfo(binfile, source_code_prefix=""):
    """ Process the bin to get the info and function"""
    file_cache = {}
    if source_code_prefix:
        for f in glob.glob(source_code_prefix + '/**/*', recursive=True):
            if os.path.isfile(f) and ".git" not in f and len(os.path.basename(f))>3:
                try:
                    with open(f, 'r', encoding="utf-8") as source_f:
                        assert os.path.basename(f).lower() not in file_cache.keys()
                        file_cache[f] = source_f.readlines()
                except Exception as e:
                    try:
                        with open(f, 'r', encoding="utf-16") as source_f:
                            assert os.path.basename(f).lower() not in file_cache.keys()
                            file_cache[f] = source_f.readlines()
                    except Exception as e:
                        pass

    if len(file_cache.keys())<1:
        return {}, {}, ""

    # binfile = binfile.replace("/", "\\")
    binfolder = os.path.dirname(binfile)
    binfile = binfile.split("\\")[-1]
    print("BINFILE", binfile, binfolder)
    # cmd_args = [
    #     "powershell", "-Command", "Dia2Dump", "-lines", "*", f"'{binfile}'"
    # ]
    cmd = f"Dia2Dump -lines * {binfile}"
    out, _err, _exit_code = cmd_with_output(cmd, cwd=binfolder)
    file_cache = {}
    # out, _err, _exit_code = cmd_with_output(cmd_args, platform='windows')
    # print(cmd, out, _err)
    try:
        lines_notclean = out.decode().split("\r\n")
    except:
        logging.info("Dia2dump error")
        lines_notclean = []
    lines = []
    for line in lines_notclean:
        lines.append(line.strip())

    lines = []
    for line in lines_notclean:
        lines.append(line.strip())
    funcs_infos = {}
    rva_seg_length = 0
    dbg_seg_length = 0
    source_file = ""
    lines_infos = {}
    file_hash_lookup = {}
    for i, line in enumerate(lines):
        lines_dict = {}
        if line.startswith("**"):
            func_name = line.replace("**", "").replace(" ", "").strip()
            rva_seg_length = 0
            dbg_seg_length = 0
            func_name_infoitem = {}
        if line.startswith("line"):
            if len(re.split(r"\w:\\", line)) == 2:
                source_file = re.findall(r"\w:\\", line)[0] + re.split(r"\w:\\", line)[1]
                if "MD5" in source_file:
                    source_file_cleaned = source_file.split(" (MD5: ")[0]
                    source_file_md5 = source_file.split(" (MD5: ")[1].replace(")", "")
                    file_hash_lookup[source_file_cleaned.strip()]=source_file_md5
                if "0x3" in source_file:
                    source_file_cleaned = source_file.split(" (0x3: ")[0]
                    source_file_md5 = source_file.split(" (0x3: ")[1].replace(")", "")
                    file_hash_lookup[source_file_cleaned.strip()]=source_file_md5
            rva = re.findall(r"at \[\w+\]", line)[0].replace("at ", "").replace("[", "").replace("]", "")
            length = int(re.findall(r"len \= \w+", line)[0].replace("len = ", ""), 16)
            line_number = int(re.findall(r"line \d+", line)[0].replace("line ", ""))
            lines_dict["line_number"] = line_number
            lines_dict["rva"] = rva
            lines_dict["length"] = length
            lines_dict["source_code"] = ""
            if source_file_cleaned not in file_cache.keys():
                try:
                    file_cache[source_file_cleaned] = open(source_file_cleaned, 'r', encoding="utf-8", errors="ignore").readlines()
                except:
                    file_cache[source_file_cleaned] = [""]
            filecontent = file_cache[source_file_cleaned]
            if len(filecontent)>line_number-1:
                lines_dict["source_code"] = filecontent[line_number-1].strip()
            
            lines_dict["source_file"] = source_file

            if "rva_start" not in func_name_infoitem.keys():
                func_name_infoitem["rva_start"] = rva
            if line_number > 10000000:
                dbg_seg_length = dbg_seg_length + length
            rva_seg_length = rva_seg_length + length
            if i+1<len(lines) and (not lines[i + 1].startswith("line")):
                func_name_infoitem["rva_end"] = str(
                    hex(int(rva, 16) + int(length))).replace("0x", "").rjust(
                        len(rva), "0")
                if func_name in funcs_infos.keys():
                    funcs_infos[func_name].append(func_name_infoitem)
                else:
                    funcs_infos[func_name] = [func_name_infoitem]
            if func_name in lines_infos.keys():
                lines_infos[func_name].append(lines_dict)
            else:
                lines_infos[func_name] = [lines_dict]
    return funcs_infos, lines_infos, source_file


def dia_list_binaries(dest_binfolder):
    """ get binary file under the binfolder """
    bfiles = []
    for single_file in glob.glob(dest_binfolder + '/**/*', recursive=True):
        if os.path.isfile(single_file) and (single_file.lower().endswith("pdb") or single_file.lower().endswith("exe") or single_file.lower().endswith("dll") or single_file.lower().endswith("lib")):
            bfiles.append(single_file)
    return bfiles


def post_processing_pdb(dest_binfolder, build_mode, library, repoinfo, toolset,
                        optimization, source_codedir="", commit="", movedir=""):
    """ Postprocess the pdb """
    bin_files = dia_list_binaries(dest_binfolder)
    outer_list = []
    func_cache = {}
    if not os.path.isdir(movedir):
        os.makedirs(movedir)
    for _, binfile in enumerate(bin_files):
        print("Moving", binfile, os.path.join(movedir, os.path.basename(binfile)))
        shutil.copy(binfile, os.path.join(movedir, os.path.basename(binfile)))

        funcs_infos, lines_infos, source_file = dia_get_func_funcinfo(binfile, source_codedir)
        item_dict = {}
        item_dict["functions"] = []
        item_dict["file"] = binfile
        for func_name, infos in funcs_infos.items():
            functions_val = {}
            functions_val["function_name"] = func_name
            functions_val["source_file"] = source_file
            if len(infos) == 1:
                functions_val["intersect_ratio"] = "0%"
            else:
                rva_segs = []
                for info_dict in infos:
                    rva_segs.append(
                        (info_dict["rva_start"], info_dict["rva_end"]))
                rva_segs.sort()
                rva_len = int(rva_segs[-1][1], 16) - int(rva_segs[0][0], 16)
                rva_gap = 0
                for k in range(0, len(rva_segs) - 1):
                    rva_gap += int(rva_segs[k+1][0], 16) - \
                        int(rva_segs[k][1], 16)
                functions_val["intersect_ratio"] = str(
                    (rva_gap / rva_len) * 100)[:5] + "%"
            functions_val["function_info"] = funcs_infos[func_name]
            functions_val["lines"] = lines_infos[func_name]
            if len(functions_val["lines"])>0:
                functions_val["source_file"] = functions_val["lines"][0]["source_file"]

            
            if "MD5" in functions_val["source_file"]:
                source_file_cleaned = functions_val["source_file"].split(" (MD5: ")[0]
            elif " (0x3: " in functions_val["source_file"]:
                source_file_cleaned = functions_val["source_file"].split(" (0x3: ")[0]
            else:
                source_file_cleaned = functions_val["source_file"]

            if source_file_cleaned not in func_cache.keys():
                func_cache[source_file_cleaned] = get_functions(source_file_cleaned)
            funcsourceinfo = func_cache[source_file_cleaned]
            for func in funcsourceinfo:
                # print("FUNC looking for", func_name, func[0])     
                if "::" in func_name and "::" in func[0]:
                    pass
                elif "::" in func_name:
                    func_name = func_name.split("::")[-1]
                elif "::" in func[0]:
                    func[0] = func[0].split("::")[-1]
                if func[0].lower() == func_name.lower():
                    # print("FUNC matched function name", filter_ascii(func[0]).lower(), filter_ascii(func_name).lower())
                    # print(func)
                    # name, startline, endline, def, top comments, body, body comment, prototype
                    functions_val["ctag_definitions"] = func[3]
                    functions_val["top_comments"] = func[4]
                    # functions_val["body_comments"] = func[6]
                    # functions_val["source_codes_ctags"] = func[5]
                    functions_val["prototype"] = func[7]
                    functions_val["source_codes"] = func[9]

                    for line_info_captured in functions_val["lines"]:
                        if (not line_info_captured["source_code"]) and (line_info_captured["line_number"] in func[8].keys()):
                            line_info_captured["source_code"] = func[8][line_info_captured["line_number"]]
                            break
                    break

            # for filename, funcsourceinfo in func_cache.items():
            #     # print("Matching", filename, functions_val["source_file"])
            #     # filename : /assemblage/assemblage_recover/assemblage_tmp/xxx/xxx/xxx.cpp
            #     # source_file_cleaned : c:\\\assemblage\\\builds\\\xx\\\xx-master\\\x.cpp (MD5: 5E7541B4C6EF43A6D29DB6964B29C554)
            #     if "program files (x86)" in functions_val["source_file"] or "d:" in functions_val["source_file"]:
            #         break
            #     source_file_path = [x.lower() for x in PureWindowsPath(source_file_cleaned).parts][::-1]
            #     filename_path = [x.lower() for x in PurePosixPath(filename).parts][::-1]
            #     # print("FUNC Matching", source_file_path, filename_path)
            #     if len(source_file_path)>0 and len(filename_path)>0 and\
            #         source_file_path[0] == filename_path[0]:
            #         # print("FUNC matched file name", source_file_path, filename_path)
            #         # print("FUNC looking for", func_name, funcsourceinfo) 
            #         for func in funcsourceinfo:
            #             # print("FUNC looking for", func_name, func[0])     
            #             if "::" in func_name and "::" in func[0]:
            #                 pass
            #             elif "::" in func_name:
            #                 func_name = func_name.split("::")[-1]
            #             elif "::" in func[0]:
            #                 func[0] = func[0].split("::")[-1]
            #             if func[0].lower() == func_name.lower():
            #                 # print("FUNC matched function name", filter_ascii(func[0]).lower(), filter_ascii(func_name).lower())
            #                 # print(func)
            #                 # name, startline, endline, def, top comments, body, body comment, prototype
            #                 functions_val["ctag_definitions"] = func[3]
            #                 functions_val["top_comments"] = func[4]
            #                 # functions_val["body_comments"] = func[6]
            #                 # functions_val["source_codes_ctags"] = func[5]
            #                 functions_val["prototype"] = func[7]
            #                 functions_val["source_codes"] = func[9]

            #                 for line_info_captured in functions_val["lines"]:
            #                     if (not line_info_captured["source_code"]) and (line_info_captured["line_number"] in func[8].keys()):
            #                         line_info_captured["source_code"] = func[8][line_info_captured["line_number"]]
            #                         break
            #                 break
            item_dict["functions"].append(functions_val)
        outer_list.append(item_dict)
    try:
        json_di = {}
        json_di["Platform"] = library
        json_di["Build_mode"] = build_mode
        json_di["Toolset_version"] = toolset
        json_di["URL"] = repoinfo["url"]
        json_di["Binary_info_list"] = outer_list
        json_di["Optimization"] = optimization
        json_di["Pushed_at"] = repoinfo["updated_at"]
        json_di["Commit"] = commit
        with open(os.path.join(dest_binfolder, PDBJSONNAME), "w") as outfile:
            json.dump(json_di, outfile, sort_keys=False, indent=4)
    except FileNotFoundError:
        logging.info("Pdbjsonfile not found")
    if not os.path.isdir(movedir):
        os.makedirs(movedir)
    shutil.move(os.path.join(dest_binfolder, PDBJSONNAME), movedir)




def post_processing_compress(dest_binfolder, repo, build_opt, num):
    """ Compress the binary file """
    repo_fname = dest_binfolder.split("\\")[-1]
    zipname = str(repo["repo_id"])+"_"+str(build_opt)+"_"+str(num)
    if os.name == "nt":
        cmd = f"cd {BINPATH}/{repo_fname}&&7z a -r -tzip {zipname}.zip *"
        out, _err, _exit_code = cmd_with_output(cmd, platform='windows')
    else:
        cmd = f"cd {BINPATH}&&zip -r {zipname}.zip {repo_fname}"
        out, _err, _exit_code = cmd_with_output(cmd, platform='linux')
    logging.info("Compress output %s", zipname)
    return f"{zipname}.zip"



def post_processing_s3(dest_url, file_location):
    sesh = boto3.Session(profile_name='assemblage')
    s3 = sesh.client('s3')
    try:
        logging.info("Sending file_location %s to %s.",
                     file_location, dest_url)
        s3.upload_file(file_location, 'assemblage-data', dest_url,
                       ExtraArgs={'StorageClass': 'STANDARD_IA'})
    except ClientError as e:
        logging.error(e)
        return ""
    return dest_url


def clean(folders, platform):
    """ Delete the dirs to free space """
    if platform == 'windows':
        for folder in folders:
            folder_name_cleaned = os.path.abspath(folder)
            try:
                _out, _err, _exit_code = cmd_with_output(
                    f"DEL /F/Q/S {folder_name_cleaned}", platform='windows')
                _out, _err, _exit_code = cmd_with_output(
                    f"RMDIR /Q/S {folder_name_cleaned}", platform='windows')
                logging.info("Cleaned %s", folder_name_cleaned)
            except subprocess.CalledProcessError as e:
                logging.error("Clean err %s", e.output)
            except UnicodeDecodeError:
                logging.error("Clean UnicodeDecodeError")
    elif platform == 'linux':
        for folder in folders:
            os.system(f"rm -rf {folder}")


def windows_pre_processing(Platform,
                           Buildmode,
                           Target_dir,
                           Optimization,
                           _tmp_dir,
                           VC_Version,
                           Favorsizeorspeed="",
                           Inlinefunctionexpansion="",
                           Intrinsicfunctions=""):
    """ Modifying the build file to save flags """
    files = []
    for filename in glob.iglob(Target_dir + '**/**', recursive=True):
        files.append(filename)
    slnfile = ""
    projfiles = []
    for f in files:
        if f.endswith("sln"):
            slnfile = f
        if f.endswith("vcxproj"):
            projfiles.append(f)
    if slnfile == "":
        logging.error("No solution file found")
        return "No SLN file found", BuildStatus.SUCCESS, ""
    try:
        cmd_with_output(f"devenv {slnfile} /upgrade")
        sln = Solution(slnfile)
        sln.set_config(Platform, Buildmode)
    except:
        logging.info("SLN parsing err, but continue with vcxproj files")
    try:
        for projfile in projfiles:
            projobj = Project(projfile)
            projobj.set_toolset_version(VC_Version)
            projobj.set_optimization(Optimization)
            if Favorsizeorspeed != "":
                projobj.set_favorsizeorspeed(Favorsizeorspeed)
            if Inlinefunctionexpansion != "":
                projobj.set_inlinefunctionexpansion(Inlinefunctionexpansion)
            if Intrinsicfunctions != "":
                projobj.enable_intrinsicfunctions()
            projobj.write()
            projobj_saved = Project(projfile)
            optimization_mode = ""
            if "O2" in Optimization:
                optimization_mode = "MaxSpeed"
            elif "O1" in Optimization:
                optimization_mode = "MinSpace"
            elif "Ox" in Optimization:
                optimization_mode = "Full"
            else:
                optimization_mode = "Disabled"
            logging.info("Read config: %s, correct: %s",
                         projobj_saved.get_optimization(), optimization_mode)
            assert optimization_mode == projobj_saved.get_optimization()
    except FileNotFoundError:
        logging.error("Build File not exist")
        return "Parsing FileNotFoundError", BuildStatus.FAILED, ""
    except AttributeError as err:
        logging.error("Build vcxproj file parsing error %s", str(err))
        return "Parsing AttributeError", BuildStatus.FAILED, ""
    except KeyError:
        logging.error("Build vcxproj file setting error")
        return "Parsing file key error", BuildStatus.FAILED, ""
    except AssertionError:
        return "Parsing file verification error", BuildStatus.FAILED, ""
    logging.info("Parsing success")
    return "Parsing success", BuildStatus.SUCCESS, slnfile


def generate_cmd_msbuild(target_dir,
            build_tool,
            build_mode,
            library,
            optimization,
            slnfile=None,
            platform='linux',
            compiler_version='v142'):
    """ Generate cmd to execute """
    cmd = ["powershell", "-Command", "msbuild"]
    if build_mode in ["Release", "Debug"]:
        cmd.append(f"/property:Configuration={build_mode}")
    if library == "x86" or library == "Win32":
        cmd.append("/property:Platform=x86")
    elif library == "x64":
        cmd.append("/property:Platform=x64")
    elif library == "Mixed Platforms":
        cmd.append("/property:Platform='Mixed Platforms'")
    elif library == "Any CPU":
        cmd.append("/p:Platform=Any CPU")
    # cmd.append(f"/p:PlatformToolset={compiler_version}")
    if compiler_version in ["v140", "v141"]:
        cmd.append("/p:WindowsTargetPlatformVersion= ")
    cmd.append("/maxcpucount:16")
    cmd.append("/property:PostBuildEvent= ")
    cmd.append("/property:OutDir=assemblage_outdir_bin/")
    cmd.append(f"'{slnfile}'")
    cmd = " ".join(cmd)
    logging.info("Windows cmd generated: %s", cmd)
    return cmd



def build(target_dir,
          build_tool,
          platform,
          build_mode="Release",
          library="x64",
          optimization="Od",
          tmp_dir="Builds",
          compiler_version="v142",
          favorsizeorspeed="",
          inlinefunctionexpansion="Obd",
          intrinsicfunctions=False,
          commit_hexsha=None):
    """ The actual build process """
    build_file = ""
    msg, status, build_file = windows_pre_processing(
        library, build_mode,
        target_dir, optimization, tmp_dir, compiler_version,
        favorsizeorspeed, inlinefunctionexpansion, intrinsicfunctions)

    if build_file:
        cmd = generate_cmd_msbuild(target_dir,
                    build_tool,
                    build_mode,
                    library,
                    optimization,
                    slnfile=build_file,
                    platform=platform,
                    compiler_version=compiler_version)
        out, err, exit_code = cmd_with_output(cmd, 600, platform)
        logging.info("Build complete with %s, finishing up", exit_code)
        try:
            output_dec = out.decode('utf-8')
            output_dec += err.decode('utf-8')
        except UnicodeDecodeError:
            logging.info("Build exited: %s", exit_code)
            return "UnicodeDecodeError", BuildStatus.FAILED, build_file
        if exit_code == 0:
            return 'success', BuildStatus.SUCCESS, build_file
        return output_dec, BuildStatus.FAILED, build_file


