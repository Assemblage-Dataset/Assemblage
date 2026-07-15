
FROM python:3.12-slim

RUN apt-get update && apt-get install -y \
    build-essential \
    python3-dev \
    gcc \
    libffi-dev \
    libz-dev \
    zlib1g-dev \
    libjpeg-dev \
    libpng-dev \
    libfreetype6-dev \
    libtiff-dev \
    tk-dev \
    && rm -rf /var/lib/apt/lists/*

COPY ./backend /app


WORKDIR /app
ENV PIP_USE_PEP517=1

RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH"
ENV PYTHONPATH=/app
RUN pip install --upgrade pip setuptools wheel
RUN pip install -r requirements.txt

# to do. replace with prod mode and use this in the docker-compose.yaml ( and have a docker-compose-prod.yaml)
CMD ["fastapi", "run", "/app/scripts/web_server.py"]
