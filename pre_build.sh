docker build -t assemblage-gh:base -f docker/gh/Dockerfile .
if [ $? -ne 0 ]; then 
    echo "assemblage-gh:base build failed"
    exit
fi

docker run --name assemblagh -it assemblage-gh:base gh auth login
ghid=$(docker container ls --all --filter="name=assemblagh" | sed '1d' | cut -c 1-12)
docker commit $ghid assemblage-gh:base