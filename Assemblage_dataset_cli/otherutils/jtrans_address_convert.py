import os
import json
import shutil

unzipdata_path = "windows"

import json
import pefile

def convert_hex_int(hex_str):
    return int("0x"+str(hex_str), 16) 

def convert_json(dir):
    print("Processing", dir)
    if os.path.isfile(os.path.join(dir, "addr2name.json")):
        return
    if os.path.isfile(os.path.join(dir, os.path.join(dir.split("/")[-1])+".json")):
        db = json.load(open(os.path.join(dir, dir.split("/")[-1])+".json", "r"))
    else:
        shutil.rmtree(dir)
        return
    name2addr = {}
    for file_db in db["Binary_info_list"]:
        filename = file_db["file"]
        functions = file_db["functions"]
        filename_base = os.path.basename(filename)
        for function in functions:
            function_name = function["function_name"]
            for function_info in function["function_info"]:
                rva_start = function_info["rva_start"]
                rva_end = function_info["rva_end"]
                for dirfile in os.listdir(dir):
                    if dirfile.endswith(filename_base):
                        pe_obj = pefile.PE(os.path.join(dir, dirfile), fast_load=1)
                        ph_start = pe_obj.get_physical_by_rva(convert_hex_int(rva_start))
                        if function_name in name2addr:
                            name2addr[function_name] = min(name2addr[function_name], ph_start)
                        else:
                            name2addr[function_name] = convert_hex_int(ph_start)
    addr2name = {x:y for y,x in name2addr.items()}
    with open(os.path.join(dir, "addr2name.json"), "w") as f:
        json.dump(addr2name, f)
    os.remove(os.path.join(dir, os.path.join(dir.split("/")[-1])+".json"))
    print("Processed", dir)

import multiprocessing
pool = multiprocessing.Pool(64)

for f in os.listdir(unzipdata_path):
    pool.apply_async(convert_json, args=(os.path.join(unzipdata_path, f), ))
pool.close()
pool.join() 
