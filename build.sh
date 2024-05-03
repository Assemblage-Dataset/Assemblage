#!/bin/sh
docker compose down&&docker rmi -f rabbitmq:3-management&&\
docker build -t assemblage-gcc:base -f docker/gcc/Dockerfile .&&\
docker build -t assemblage-gcc:default -f docker/gcc/gcc-default/Dockerfile .
# docker build -t assemblage-ddisasm:default -f docker/disassmbler/Dockerfile .
# sh start.sh
