
FROM  assemblage-gh:base

RUN apt-get update && apt-get -y upgrade

ENV PYENV_ROOT /root/.pyenv
ENV PATH $PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH
RUN apt-get -y update && apt-get install -y make build-essential libssl-dev zlib1g-dev libbz2-dev \
        libreadline-dev libsqlite3-dev wget ca-certificates curl llvm libncurses5-dev \
        xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
        mecab-ipadic-utf8 git libboost-all-dev cmake flex bison libzmq3-dev \
        autoconf libtool pkg-config libc++-dev libc++abi-dev bundler automake \
        g++ unzip

COPY . /assemblage

RUN pip install -r /assemblage/requirements.txt
RUN cp -r /assemblage/aws /root/.aws
RUN export AWS_CONFIG_FILE=/root/.aws/config
RUN export AWS_SHARED_CREDENTIALS_FILE=/root/.aws/credentials