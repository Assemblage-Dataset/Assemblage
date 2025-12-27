FROM stargazermiao/ddisasm

RUN apt update -y && apt install -y python3 python3-pip unzip

RUN update-alternatives --install /usr/bin/python python /usr/bin/python3 100

COPY ./backend /app

RUN pip install -r /assemblage/requirements.txt


WORKDIR /app
