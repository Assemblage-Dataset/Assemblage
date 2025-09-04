
FROM  assemblage-gh:base

RUN apt-get update && apt-get -y upgrade

ENV PYENV_ROOT /root/.pyenv
ENV PATH $PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH
RUN apt-get -y update && apt-get install -y make build-essential libssl-dev zlib1g-dev libbz2-dev \
        libreadline-dev libsqlite3-dev wget ca-certificates curl llvm libncurses-dev \
        xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
        mecab-ipadic-utf8 git libboost-all-dev cmake flex bison libzmq3-dev \
        autoconf libtool pkg-config libc++-dev libc++abi-dev bundler automake \
        g++ unzip python3-pip python3.12-venv libjpeg-dev zlib1g-dev

COPY . /assemblage

WORKDIR /assemblage
ENV PIP_USE_PEP517=1

RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH"
RUN pip install --upgrade pip setuptools wheel
RUN pip install -r requirements.txt
RUN cp -r aws /root/.aws
RUN export AWS_CONFIG_FILE=/root/.aws/config
RUN export AWS_SHARED_CREDENTIALS_FILE=/root/.aws/credentials