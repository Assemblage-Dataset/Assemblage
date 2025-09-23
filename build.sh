#!/bin/sh
docker compose down #&& docker rmi -f rabbitmq:3-management
docker build -t assemblage-gcc:base -f docker/gcc/Dockerfile . &&\
docker build -t assemblage-gcc:default -f docker/gcc/gcc-default/Dockerfile .
docker build -t assemblage-clang:base -f docker/clang/Dockerfile . && \
docker build -t assemblage-clang:default -f docker/clang/clang-default/Dockerfile .

[ -d binaries ] || mkdir binaries
 