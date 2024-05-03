#!/bin/bash

docker-compose down&&docker container prune -f&&docker rmi -f rabbitmq:3-management&&\
docker-compose up -d --remove-orphans
# docker-compose logs -tf