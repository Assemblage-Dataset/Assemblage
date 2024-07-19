import os
import glob
import re
import logging
import subprocess
import shutil
import signal
import json
import ftplib
import yaml
import random
import string
import time
import boto3

from botocore.exceptions import ClientError
from assemblage.consts import PDBJSONNAME, BINPATH
from assemblage.bootstrap import AssmeblageCluster
from assemblage.worker.scraper import GithubRepositories
from assemblage.worker.profile import AWSProfile
from assemblage.worker.postprocess import PostAnalysis
from assemblage.worker.build_method import BuildStartegy, DefaultBuildStrategy
from assemblage.windows.parsers.proj import Project
from assemblage.windows.parsers.sln import Solution

time_now = int(time.time())
start = time_now - time_now % 86400
querylap = 1440000
aws_profile = AWSProfile("assemblage-test", "assemblage")

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
    if os.path.isdir(dest_binfolder):
        files = os.listdir(dest_binfolder)
        for single_file in files:
            if single_file.endswith("exe") or single_file.endswith("dll"):
                bfiles.append(single_file)
    return bfiles


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

def post_processing_s3(dest_url, file_location, aws_profile: AWSProfile):
    sesh = boto3.Session(profile_name=aws_profile.profile_name)
    s3 = sesh.client('s3')
    try:
        logging.info("Sending file_location %s to %s.",
                     file_location, dest_url)
        s3.upload_file(file_location, aws_profile.s3_bucket_name, dest_url,
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

def get_build_system(_files):
    """Analyze build tool from file list"""
    return "sln"

a_crawler = GithubRepositories(
    git_token="",
    qualifier={
        "language:c++",
        "topic:windows",
    }, 
    crawl_time_start= start,
    crawl_time_interval=querylap,
    proxies=[],
    build_sys_callback=get_build_system
)

another_crawler = GithubRepositories(
    git_token="",
    qualifier={
        "language:c++",
        "topic:windows",
        # "stars:>10"
    }, 
    crawl_time_start= start,
    crawl_time_interval=querylap,
    proxies=[],
    build_sys_callback=get_build_system
    # sort="stars", order="desc"
)

class WindowsDefaultStrategy(DefaultBuildStrategy):

    def pre_build(self, Platform,
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
    
    def post_build_hook(self, dest_binfolder, build_mode, library, repoinfo, toolset,
                        optimization):
        """ Postprocess the pdb """
        bin_files = dia_list_binaries(dest_binfolder)
        outer_list = []
        for _, binfile in enumerate(bin_files):
            binfile_path = os.path.join(dest_binfolder, binfile)
            # logging.info("Checking binary info %s: %s", binfile,
            #              os.path.isfile(binfile))
            funcs_infos, lines_infos, source_file = dia_get_func_funcinfo(
                binfile_path)
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
            with open(os.path.join(dest_binfolder, "pdbinfo.json"), "w") as outfile:
                json.dump(json_di, outfile, sort_keys=False)
            repoid = dest_binfolder.split("\\")[-1]
            # with open(os.path.join(PDBPATH, f"{repoid}.json"), "w") as outfile:
            #     json.dump(json_di, outfile, sort_keys=False, indent=4)
        except FileNotFoundError:
            logging.info("Pdbjsonfile not found")
    

test_cluster_windows = AssmeblageCluster(name="test"). \
                build_system_analyzer(get_build_system). \
                aws(aws_profile). \
                message_broker(mq_addr="rabbitmq", mq_port=5672). \
                mysql(). \
                build_option(
                    100, platform="windows", language="c++",
                    compiler_name="v143",
                    compiler_flag="-Od",
                    build_command="Debug",
                    library="x64",
                    build_system="sln"). \
                builder(
                    "windows", "msvc", 100, docker_image="",
                    custom_build_method=DefaultBuildStrategy(),
                    aws_profile= aws_profile
                ). \
                scraper([a_crawler, another_crawler]). \
                use_new_mysql_local()


test_cluster_windows.boot()
