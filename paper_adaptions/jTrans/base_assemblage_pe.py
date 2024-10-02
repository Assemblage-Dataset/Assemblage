from elftools.elf.elffile import ELFFile
import elftools.elf.elffile as elffile
from elftools.elf.sections import SymbolTableSection
from collections import defaultdict
import os
import json
import sys
import os
import sqlite3
import glob
from tqdm import tqdm
import shutil
import pefile


dbfile = "sqlite.sqlite"

class Binarybase(object):
    def __init__(self, unstrip_path):
        self.unstrip_path = unstrip_path
        self.addr2name = self.extract_addr2name(self.unstrip_path)

    def get_func_name(self, name, functions):
        if name not in functions:
            return name
        i = 0
        while True:
            new_name = name+'_'+str(i)
            if new_name not in functions:
                return new_name
            i += 1

    def extract_addr2name(self, path):
        '''
        return:
        '''
        
        basepath = os.path.dirname(path)
        basename = os.path.basename(path)
        binid = basepath.split("/")[-1]

        connection = sqlite3.connect(dbfile)
        cursor = connection.cursor()

        functions = {}
        for x in cursor.execute("SELECT id, name FROM functions WHERE binary_id = ?", (binid,)):
            functions[x[0]] = {}
            functions[x[0]]['name'] = x[1]
        for function_id in functions.keys():
            for x in cursor.execute("SELECT start, end FROM rvas WHERE function_id = ?", (function_id,)):
                functions[function_id]['start'] = x[0]
                functions[function_id]['end'] = x[1]
        peobj =  pefile.PE(path, fast_load=True)
        for function_id in functions.keys():
            functions[function_id]['addr'] = peobj.get_physical_by_rva(functions[function_id]['start'])
        
        addr2name = {func['addr']: func['name'] for (name, func) in functions.items()}
        
        return addr2name
