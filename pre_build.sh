docker build -t assemblage-gh:base -f docker/gh/Dockerfile .
docker run --name assemblage_gh -it assemblage-gh:base gh auth login
ghid=$(docker container ls --all --filter="name=assemblage_gh" | sed '1d' | cut -c 1-12)

docker commit $ghid assemblage-gh:base
docker container stop $ghid
docker container rm assemblage_gh

docker run --name assemblage_gh -it assemblage-gh:base aws configure
ghid=$(docker container ls --all --filter="name=assemblage_gh" | sed '1d' | cut -c 1-12)
docker commit $ghid assemblage-gh:base
docker container stop $ghid
docker container rm assemblage_gh
