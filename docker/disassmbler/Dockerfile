FROM stargazermiao/ddisasm

RUN apt update -y && apt install -y python3 python3-pip unzip

RUN update-alternatives --install /usr/bin/python python /usr/bin/python3 100

COPY . /assemblage

RUN pip install -r /assemblage/requirements.txt
RUN cp -r /assemblage/aws /root/.aws
RUN export AWS_CONFIG_FILE=/root/.aws/config
RUN export AWS_SHARED_CREDENTIALS_FILE=/root/.aws/credentials

WORKDIR /assemblage
