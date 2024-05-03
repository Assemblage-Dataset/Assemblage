"""
Stat Script for coordinator

For windows:
config.json in worker and coordinator
"grpc_addr" : "127.0.0.1:50051",
"db_path" : "mysql+pymysql://root:YourPass@localhost:3306/ghtorrent?charset=utf8mb4"
"""

import logging
import argparse
import json

from assemblage.coordinator.coordinator import Coordinator
logging.basicConfig(level=logging.DEBUG)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Coordinator Node of assemblage')
    parser.add_argument('--config', metavar='config_file', type=str, required=True,
                        help='path to configure file.')
    args = parser.parse_args()
    with open(args.config, 'r') as f:
        config = json.loads(f.read())
        cor = Coordinator(config['rabbitmq_host'],
                          config['rabbitmq_port'],
                          config['grpc_addr'],
                          config['db_path'],
                          config['cluster_name'],
                          config['aws_mode'])
        cor.run()
