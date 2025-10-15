'''
New Entry Point for Assemblage Functions


'''
import os
import sys
from assemblage.consts import WorkerType
from assemblage.config import BuilderSettings, CoordinatorSettings, ScraperSettings
from assemblage.coordinator.coordinator import Coordinator
from assemblage.worker.builder import Builder
from assemblage.worker.scraper import Scraper
import threading



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
            # settings = CoordinatorSettings()
            # coordinator = Coordinator()
            # coordinator.run()
            # call start coordinator
        case WorkerType.Builder:
            print("Starting Builder")
            settings = BuilderSettings()
            print(settings.runtime_env)
            builder = Builder(settings=settings, opt_id=0)
            builder.run()
            # call start builder
        case WorkerType.Scraper:
            print("Starting Scraper")
            settings = ScraperSettings()
            #print(settings.dict())
            scraper = Scraper(settings=settings, workerid=0)
            scraper.run()
            # call start scraper

            # multithread staggered execution example
            '''
            scraper1 = Scraper(settings=settings, workerid=0)
            settings2 = ScraperSettings()
            settings2.start_time -= 31556952 # have this scraper start a year earlier than the other
            scraper2 = Scraper(settings=settings2, workerid=1)
            t1 = threading.Thread(target=scraper1.run)
            t2 = threading.Thread(target=scraper2.run)
            t1.start()
            t2.start()
            t1.join()
            t2.join()
            '''
