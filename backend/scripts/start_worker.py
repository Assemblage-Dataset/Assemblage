'''
New Entry Point for Assemblage Functions


'''
import os
import logging
import logging.config
import sys
from assemblage.consts import WorkerType
from assemblage.config import BuilderSettings, CoordinatorSettings, ScraperSettings
from assemblage.coordinator.coordinator import Coordinator
from assemblage.worker.builder import Builder
from assemblage.worker.scraper import Scraper

# 'DEBUG' if self.runtime_env == RuntimeEnv.dev else 'INFO'




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
            settings = CoordinatorSettings()         
            # i dont particularly like this but the dict config isnt working...   
            logging.basicConfig(format="%(asctime)s %(levelname)s:%(message)s", datefmt="%Y-%m-%d %H:%M:%S", level=settings.logLevel)
            coordinator = Coordinator(settings)
            coordinator.run()
            # call start coordinator
        case WorkerType.Builder:
            settings = BuilderSettings()
            logging.basicConfig(format="%(asctime)s %(levelname)s:%(message)s", datefmt="%Y-%m-%d %H:%M:%S", level=settings.logLevel)
            builder = Builder(settings=settings, opt_id=0)
            builder.run()
            # call start builder
        case WorkerType.Scraper:
            settings = ScraperSettings()
            logging.basicConfig(format="%(asctime)s %(levelname)s:%(message)s", datefmt="%Y-%m-%d %H:%M:%S", level=settings.logLevel)

            # scraper = Scraper()
            # scraper.run()
            # call start scraper
