FROM ubuntu:noble

ENV PYTHON_VERSION 3.9.1
RUN apt-get update && apt-get -y upgrade
RUN apt-get install -y gnupg software-properties-common curl

# from https://github.com/cli/cli/blob/trunk/docs/install_linux.md
RUN curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg | dd of=/usr/share/keyrings/githubcli-archive-keyring.gpg
RUN echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" | tee /etc/apt/sources.list.d/github-cli.list > /dev/null
RUN apt-get update
RUN apt-get install -y gh
RUN apt-get update && apt-get -y upgrade
RUN DEBIAN_FRONTEND="noninteractive" apt-get -y install tzdata

# set up grpc
# ENV GRPC_RELEASE_TAG v1.12.x
RUN rm -rf /var/local/git/grpc
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
