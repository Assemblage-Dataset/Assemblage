
FROM  stargazermiao/gclang

COPY ./backend/ /assemblage

RUN pip install -r requirements.txt
COPY ./aws /root/.aws
RUN export AWS_CONFIG_FILE=/root/.aws/config
RUN export AWS_SHARED_CREDENTIALS_FILE=/root/.aws/credentials
