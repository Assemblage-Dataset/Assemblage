
FROM  assemblage-gcc:base

COPY . /assemblage

RUN pip install -r /assemblage/requirements.txt
RUN cp -r /assemblage/aws /root/.aws
RUN export AWS_CONFIG_FILE=/root/.aws/config
RUN export AWS_SHARED_CREDENTIALS_FILE=/root/.aws/credentials
