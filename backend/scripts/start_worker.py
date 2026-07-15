"""
New Entry Point for Assemblage Functions


"""

import logging
import logging.config
import os
import sys
import time

from assemblage.config import BuilderSettings, CoordinatorSettings, ScraperSettings
from assemblage.consts import WorkerType
from assemblage.coordinator.coordinator import Coordinator
from assemblage.worker.builder import Builder
from assemblage.worker.scraper import Scraper

if __name__ == "__main__":
    worker_type_env = os.getenv("TYPE")

    if not worker_type_env:
        print(f"""
              ERROR: PLEASE SET TYPE ENV VALUE. Options are {[value.value for value in WorkerType]}\n.
              EXAMPLE: TYPE=coordinator
              """)
        sys.exit(1)
    try:
        worker_type = WorkerType(worker_type_env)
    except ValueError:
        print(f"""
              ERROR: INVALID TYPE ENV VARIABLE SET. Options are {[value.value for value in WorkerType]}
              EXAMPLE: TYPE=coordinator
              """)
        sys.exit(1)

    match worker_type:
        case WorkerType.Coordinator:
            settings = CoordinatorSettings()
            # i dont particularly like this but the dict config isnt working...
            logging.basicConfig(
                format="%(asctime)s %(levelname)s:%(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
                level=settings.logLevel,
            )
            coordinator = Coordinator(settings)
            coordinator.run()
            # call start coordinator
        case WorkerType.Builder:
            settings = BuilderSettings()
            logging.basicConfig(
                format="%(asctime)s %(levelname)s:%(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
                level=settings.logLevel,
            )
            builder = Builder(settings=settings)
            builder.run()
            # call start builder
        case WorkerType.Scraper:
            settings = ScraperSettings()
            # print(settings.dict())
            scraper = Scraper(settings=settings, workerid=0)
            logging.basicConfig(
                format="%(asctime)s %(levelname)s:%(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
                level=settings.logLevel,
            )  # if i could figure out how to set this in the config that would be much better but alas. no
            scraper.run()
            # call start scraper
        case WorkerType.LegacyConan:
            # DeepHistory legacy builder - standalone Conan-based pipeline
            # Does NOT use RabbitMQ/coordinator. Reads manifest, builds via Conan, writes to SQLite.
            # Pass CLI args via DEEPHISTORY_ARGS env or use docker-compose-deephistory.yml
            import shlex

            logging.basicConfig(
                format="%(asctime)s %(levelname)s:%(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
                level="INFO",
            )
            deephistory_args = os.getenv("DEEPHISTORY_ARGS", "")
            argv_override = shlex.split(deephistory_args) if deephistory_args else sys.argv[1:]
            sys.argv = [sys.argv[0]] + argv_override
            from scripts.build_deephistory import main as deephistory_main

            deephistory_main()
        case WorkerType.Test:
            while True:
                time.sleep(1)

        # to run multiple instances of the same worker, add multiple instances in docker compose
