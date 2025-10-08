"""
find binary file inside a directory
Yihao Sun
"""
import logging
import os

from elftools.elf.elffile import ELFFile
from elftools.common.exceptions import ELFError

from assemblage.config import Settings

logger = logging.getLogger(__name__)


settings = Settings()
def find_elf_bin(path: str) -> set:
    """ Find elf files and executables """
    logger.info(f"Finding elf files and executables in {path}")
    file_paths = set()
    for root, dirs, file_names in os.walk(os.path.realpath(path)):
        if '.git' in dirs: # skip .git files 
            dirs.remove('.git') 

        for file_name in file_names:
            location = f'{root}/{file_name}'
            if not os.path.exists(location):
                continue
            try:
                if location.endswith(('.s', '.ii', '.bc', '.S', '.obj')) and settings.SAVE_ASSEMBLY:
                    file_paths.add(location)
                    continue
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
