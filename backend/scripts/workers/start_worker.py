'''
New Entry Point for Assemblage Functions


'''
import os
import sys
from assemblage.consts import WorkerType

from assemblage.config import CoordinatorSettings, BuilderSettings, ScraperSettings






if __name__ == "__main__":
    
    worker_type_env = os.getenv("TYPE")
    
    if not worker_type_env:
        print(f'''
              ERROR: PLEASE SET TYPE ENV VALUE. Options are {[value.value for value in WorkerType]}\n.
              EXAMPLE: TYPE=coordinator
              ''')
        sys.exit(1)
    try: 
        worker_type = WorkerType(worker_type_env)
    except ValueError: 
        print(f'''
              ERROR: INVALID TYPE ENV VARIABLE SET. Options are {[value.value for value in WorkerType]}
              EXAMPLE: TYPE=coordinator
              ''')
        sys.exit(1)
    
    match worker_type: 
        case WorkerType.Coordinator:
            print("Starting Coordinator")
            # call start coordinator
        case WorkerType.Builder:
            print("Starting Builder")
            # call start builder
        case WorkerType.Scraper:
            print("Starting Scraper")

            # call start scraper
