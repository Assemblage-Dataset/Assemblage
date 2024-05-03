FROM assemblage-gh:base

RUN apt-get update && apt-get -y upgrade

RUN DEBIAN_FRONTEND="noninteractive" apt-get -y install tzdata

RUN apt-get -y update && apt-get install -y make build-essential libssl-dev zlib1g-dev libbz2-dev \
        libreadline-dev libsqlite3-dev wget ca-certificates curl llvm libncurses5-dev \
        xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
        mecab-ipadic-utf8 git libboost-all-dev cmake flex bison libzmq3-dev \
        autoconf libtool pkg-config libc++-dev libc++abi-dev bundler automake \
        g++ unzip

# set up grpc
# ENV GRPC_RELEASE_TAG v1.12.x
RUN git clone https://github.com/grpc/grpc /var/local/git/grpc --recurse-submodules && \
    cd /var/local/git/grpc && \
    git submodule update --init && \
    mkdir -p cmake/build && \
    cd cmake/build && \
    cmake ../.. && \
    make -j8 && make install

# setup python

ENV PYENV_ROOT /root/.pyenv
ENV PATH $PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH

RUN set -ex \
    && curl https://pyenv.run | bash \
    && pyenv update \
    && pyenv install $PYTHON_VERSION \
    && pyenv global $PYTHON_VERSION \
    && pyenv rehash

RUN apt-get -y update && DEBIAN_FRONTEND="noninteractive" apt-get install -y rebar \
                libhdf5-dev exuberant-ctags postgresql postgresql-server-dev-all \
                libglu1-mesa-dev freeglut3-dev mesa-common-dev \
                python3-opencv \
                openjdk-11-jdk \
                ocaml \
                erlang \
                mono-xbuild \
                qtbase5-dev qtchooser qt5-qmake qtbase5-dev-tools \
                swig \
                tree

RUN curl -fsSL https://deb.nodesource.com/setup_current.x | bash - && apt-get update && apt-get install -y nodejs

RUN curl https://raw.githubusercontent.com/pshved/timeout/master/timeout | \
    tee /usr/local/bin/timeout && chmod 755 /usr/local/bin/timeout

COPY . /assemblage

RUN pip install -r /assemblage/requirements.txt
RUN cp -r /assemblage/aws /root/.aws
RUN export AWS_CONFIG_FILE=/root/.aws/config
RUN export AWS_SHARED_CREDENTIALS_FILE=/root/.aws/credentials