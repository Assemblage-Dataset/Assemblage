"""
Assemblage Binary extractor
"""
import os
import json
import pefile

import logging


# Logging configs
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler("Assemblage_loader.log"),
        logging.StreamHandler()
    ]
)


class DisposableLoader:
    """ Binary loader """

    def __init__(self):
        self.json = {}
        self.bin_files = []
        self.working_dir = ""
        self.metadata = {}

    def loadjson(self, jsonfile):
        """ Load json and store data """
        assert os.path.isfile(jsonfile)
        with open(jsonfile, 'r') as json_file:
            self.json = json.load(json_file)
        self.working_dir = os.path.dirname(jsonfile)
        for bininfo in self.json["Binary_info_list"]:
            path = bininfo["file"]
            self.bin_files.append(path.split("\\")[-1])
        for k, v in self.json.items():
            if k != "Binary_info_list":
                self.metadata[k] = v

    def peek(self):
        """ Peek the metadata """
        print(self.metadata)

    def get_meta(self):
        """ Get metadata """
        return self.metadata

    def get_files(self):
        """ Get all bin files """
        return self.bin_files

    def get_functions(self, bin_file):
        """ Get functions from binfile """
        funcs = []
        for bin_item in self.json["Binary_info_list"]:
            if bin_item["file"].endswith(bin_file):
                for func_infos in bin_item["functions"]:
                    funcs.append(func_infos["function_name"])
                break
        return funcs

    def get_bytes_list(self, bin_file, func_name):
        """ Get bytes from binary for function, return list """
        locations = []
        datas = []
        bin_info = {}
        for bin_item in self.json["Binary_info_list"]:
            if bin_item["file"].endswith(bin_file):
                bin_info = bin_item
                bin_file = os.path.join(self.working_dir, bin_file)
        for function in bin_info["functions"]:
            if function["function_name"] == func_name:
                for location_infos in function["function_info"]:
                    locations.append(
                        (location_infos["rva_start"], location_infos["rva_end"]))
        for (rva_start, rva_end) in locations:
            assert os.path.isfile(bin_file)
            pe = pefile.PE(bin_file)
            rva_start = int(rva_start, 16)
            rva_end = int(rva_end, 16)
            data = pe.get_memory_mapped_image()[rva_start:rva_end]
            datas.append(data)
        return datas

    def get_bytes_contiguous(self, bin_file, func_name):
        """ Get bytes from binary for function, return all bytes """
        locations = []
        bin_info = {}
        for bin_item in self.json["Binary_info_list"]:
            if bin_item["file"].endswith(bin_file):
                bin_info = bin_item
                bin_file = os.path.join(self.working_dir, bin_file)
        for function in bin_info["functions"]:
            if function["function_name"] == func_name:
                for location_infos in function["function_info"]:
                    locations.append(int(location_infos["rva_start"], 16))
                    locations.append(int(location_infos["rva_end"], 16))
        rva_start = min(locations)
        rva_end = max(locations)
        if not os.path.isfile(bin_file):
            bin_file = bin_file.split("/")[-1]
        assert os.path.isfile(bin_file)
        pe = pefile.PE(bin_file)
        data = pe.get_memory_mapped_image()[rva_start:rva_end]
        return data


class Loader:
    def __init__(self):
        self.loader = DisposableLoader()
        self.dir = ""
        logging.info("Loader Init")

    def setdir(self, dir):
        logging.info("Setdir %s", dir)
        subfolders = 0
        try:
            subfolders = len(os.listdir(dir))
        except FileNotFoundError:
            logging.error("Setdir Folder not found")
        logging.info("%s folders found", subfolders)
        self.dir = dir

    def load(self, limit=2147483647):
        logging.info("Loading...")
        files = []
        functions = []
        function_bytes = []
        try:
            files = os.listdir(self.dir)
        except FileNotFoundError:
            logging.error("Folder %s not found", self.dir)
        for afile in files:
            logging.info("%s functions loaded", len(function_bytes))
            if len(function_bytes) > limit:
                return functions, function_bytes
            loader = DisposableLoader()
            try:
                loader.loadjson(os.path.join(
                    self.dir, afile, "pdbinfo.json"))
                binary_files = loader.get_files()
                for binary_file in binary_files:
                    bin_function_names = loader.get_functions(binary_file)
                    for onefunction in bin_function_names:
                        functions.append(onefunction)
                        function_bytes.append(
                            loader.get_bytes_contiguous(binary_file, onefunction))
            except AssertionError:
                logging.error("File or json not found in %s", afile)
        return functions, function_bytes
