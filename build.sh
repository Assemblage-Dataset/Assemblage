#!/bin/sh
docker build -t assemblage-gh:base -f docker/gh/Dockerfile .
docker run --name assemblagh -it assemblage-gh:base gh auth login
ghid=$(docker container ls --all --filter="name=assemblagh" | sed '1d' | cut -c 1-12)
docker commit $ghid assemblage-gh:base
docker compose down&&docker rmi -f rabbitmq:3-management&&\
docker build -t assemblage-gcc:base -f docker/gcc/Dockerfile .&&\
docker build -t assemblage-gcc:default -f docker/gcc/gcc-default/Dockerfile .

