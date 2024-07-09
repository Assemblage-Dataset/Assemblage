"""
find binary file inside a directory
Yihao Sun
"""
import os

from elftools.elf.elffile import ELFFile
from elftools.common.exceptions import ELFError


def find_elf_bin(path: str) -> set:
    """ Find elf files and executables """
    file_paths = set()
    for root, _, file_names in os.walk(os.path.realpath(path)):
        for file_name in file_names:
            location = f'{root}/{file_name}'
            if not os.path.exists(location):
                continue
            try:
                with open(location, 'rb') as f:
                    try:
                        ef = ELFFile(f)
                        if ef.header['e_type'] == 'ET_EXEC' or ef.header['e_type'] == 'ET_DYN':
                            file_paths.add(location)
                    except ELFError:
                        continue
            except OSError:
                continue
    return file_paths


if __name__ == '__main__':
    print(list(find_elf_bin('./')))
