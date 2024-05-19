'''
Start script for worker

Yihao Sun
'''

import argparse
import json
import os
import socket
import tempfile
import threading
import time
import logging
import random

import boto3
import grpc
from assemblage.protobufs.assemblage_pb2_grpc import AssemblageServiceStub

from assemblage.worker.scraper import Scraper
from assemblage.worker.builder import Builder
from assemblage.worker.disasm import DDisasmWorker, Dia2dumpProcessor
from assemblage.consts import BUILDPATH

# TODO: make this configurable?
send_binary_method = "S3"


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Worker Node of assemblage')
    parser.add_argument('--config', metavar='config_file', type=str,
                        help='path to configure file.')
    parser.add_argument('--type', metavar='type', type=str, default='builder',
                        help='type of worker, current can be scraper or builder')
    parser.add_argument('--number', metavar='type', type=int, default=1,
                        help='number of worker to run')
    parser.add_argument('--s3', metavar="s3", type=str,
                        help='aws s3 bucket, if worker will pull config file on aws s3')

    args = parser.parse_args()
    config_file_path = args.config

    if args.type == 'builder':
        with open(config_file_path, 'r') as f, tempfile.TemporaryDirectory() as tmp_dir:
            config = json.loads(f.read())
            logging.info(config)
            with grpc.insecure_channel(config['grpc_addr']) as channel:
                if os.name == 'nt':
                    while True:
                        workers = [object() for i in range(args.number)]
                        threads = [object() for i in range(args.number)]
                        for i in range(args.number):
                            proxy_server = None
                            if 'clone_proxy' in config.keys():
                                proxy_server = config['clone_proxy']
                            workers[i] = Builder(
                                config['rabbitmq_host'],
                                config['rabbitmq_port'],
                                AssemblageServiceStub(channel),
                                "builder;windows",
                                platform="windows",
                                build_mode=config["build_mode"],
                                opt_id=config['default_build_opt'],
                                tmp_dir=BUILDPATH,
                                library=config['library'],
                                compiler=config['compiler'],
                                compiler_flag=config['optimization'],
                                random_pick=config['random_pick'],
                                proxy_clone_servers=proxy_server,
                                proxy_token=config['clone_proxy_token'])
                            threads[i] = threading.Thread(
                                target=workers[i].run)
                        for t in threads:
                            time.sleep(1)
                            t.start()
                        time.sleep(600)
                else:
                    with open(config_file_path, 'r') as f:
                        config = json.loads(f.read())
                        logging.info(config)
                    while True:
                        worker = Builder(
                            config['rabbitmq_host'],
                            config['rabbitmq_port'],
                            rpc_stub=AssemblageServiceStub(channel),
                            worker_type="builder;linux",
                            platform="linux",
                            opt_id=config['build_opt'],
                            blacklist=config['blacklist'],
                            compiler=config['compiler'],
                            send_binary_method=send_binary_method
                        )
                        worker.run()
                        time.sleep(600)
    elif args.type == 'scraper':
        with open(config_file_path, 'r') as f, tempfile.TemporaryDirectory() as tmp_dir:
            config = json.loads(f.read())
            workers = [object() for i in range(len(config['git_token']))]
            threads = [object() for i in range(len(config['git_token']))]
            time_now = int(time.time())
            querylap = 3600*8
            for i in range(len(workers)):
                start = time_now - time_now % 86400
                start = random.randint(1262304000, start)
                workers[i] = Scraper(
                    rabbitmq_host=config['rabbitmq_host'],
                    rabbitmq_port=config['rabbitmq_port'],
                    tmp_dir=tmp_dir,
                    lang=config['language'],
                    git_token=config['git_token'][i],
                    workerid=i,
                    sln_only=True,
                    crawl_time_start=start,
                    crawl_time_interval=querylap,
                    crawl_time_lap=querylap,
                    proxies=config['proxies'])
                start -= querylap
                threads[i] = threading.Thread(target=workers[i].run)
            for t in threads:
                t.start()
    elif args.type == 'disasm':
        with open(config_file_path, 'r') as f:
            config = json.loads(f.read())
            with grpc.insecure_channel(config['grpc_addr']) as channel:
                worker = DDisasmWorker(
                    rabbitmq_host=config['rabbitmq_host'],
                    rabbitmq_port=config['rabbitmq_port'],
                    rpc_stub=AssemblageServiceStub(channel),
                    worker_type=";linux",
                    opt_id=1,
                    send_binary_method=send_binary_method,
                )
                worker.run()
    elif args.type == 'dia2dump':
        with open(config_file_path, 'r') as f:
            config = json.loads(f.read())
            with grpc.insecure_channel(config['grpc_addr']) as channel:
                worker = Dia2dumpProcessor(
                    rabbitmq_host=config['rabbitmq_host'],
                    rabbitmq_port=config['rabbitmq_port'],
                    rpc_stub=AssemblageServiceStub(channel),
                    worker_type=";linux",
                    opt_id=1,
                    send_binary_method=send_binary_method,
                )
                worker.run()
    else:
        print('not supported job type')
