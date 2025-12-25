
FROM ubuntu:noble-20250910

RUN apt-get update && apt-get -y upgrade

ENV PYENV_ROOT /root/.pyenv
ENV PATH $PYENV_ROOT/shims:$PYENV_ROOT/bin:$PATH
RUN apt-get -y update && apt-get install -y make build-essential libssl-dev zlib1g-dev libbz2-dev \
        libreadline-dev libsqlite3-dev wget ca-certificates curl llvm libncurses-dev \
        xz-utils tk-dev libxml2-dev libxmlsec1-dev libffi-dev liblzma-dev \
        mecab-ipadic-utf8 git libboost-all-dev cmake flex bison libzmq3-dev \
        autoconf libtool pkg-config libc++-dev libc++abi-dev bundler automake \
        g++ unzip python3-pip python3.12-venv libjpeg-dev zlib1g-dev clang

COPY ./backend /app

WORKDIR /app
ENV PIP_USE_PEP517=1
RUN python3 -m venv /venv
ENV PYTHONPATH=/app
ENV PATH="/venv/bin:$PATH"
RUN pip install --upgrade pip setuptools wheel
RUN pip install -r requirements.txt

ENV CC='clang'
ENV CXX='clang++'
ENV COMPILER="clang"
ENV LANGUAGE="c++"

RUN echo '2:clang' >> /conf

RUN cp /usr/bin/clang /usr/bin/cc && cp /usr/bin/clang++ /usr/bin/cc1plus && \
    cp -f /usr/bin/clang /usr/bin/gcc && cp -f /usr/bin/clang++ /usr/bin/g++

HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 CMD  [ "CMD", "bash", "-c", "pgrep -f start_worker.py > /dev/null" ]


ENTRYPOINT ["python3", "/app/scripts/start_worker.py"]
