
FROM  stargazermiao/rust

COPY ./backend /app

RUN pip install -r /assemblage/requirements.txt
COPY ./aws /root/.aws
RUN export AWS_CONFIG_FILE=/root/.aws/config
RUN export AWS_SHARED_CREDENTIALS_FILE=/root/.aws/credentials
