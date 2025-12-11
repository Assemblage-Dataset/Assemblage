# Assemblage

Assemblage is a distributed binary corpus discovery, generation, and archival tool built to provide high-quality labeled metadata for the purposes of building training data for machine learning applications of binary analysis and other applications (static / dynamic analysis, reverse engineering, etc...).  

You can now find our paper on [arxiv](https://arxiv.org/abs/2405.03991)  

## Deployment and Dataset Availability

A brief introduction to the APIs and deployment can be found [here](https://assemblagedocs.readthedocs.io/)

We include __**only**__ the subset of binaries for which permissive licenses can be ascertained, please checkout our [data sheet](https://assemblage-dataset.net/assets/total-datasheet.pdf).   
For up to date info and download, please visit the [dataset page](https://assemblagedocs.readthedocs.io/en/latest/dataset.html)

<sub>The code in this repository is published under MIT license.</sub>

NOTE. For running builders distrubuted requires the RabbitMQ server to be exposed. Currenlty, the default username/passwords are used, so we reccomend that you set up firewall rules to ensure only your worker host can access the RabbitMQ server.




## Initial Setup (Linux Build)

1. Clone the repo and install Docker. Create and configure a GitHub token (optional).

2. Within the project directory, create a secrets.env file with the following environment variables:

```
DB_HOST=assemblage-db
DB_PORT=5432
POSTGRES_DATABASE=assemblage
POSTGRES_USER=assemblage
POSTGRES_PASSWORD=<password>
MINIO_ROOT_USER=<chosen user>
MINIO_ROOT_PASSWORD=<chosen password>
GITHUB_TOKEN=<token>
```

3. Run Docker, then run the following command in the Assemblage root directory to build and run the Docker images. This will take some time. 

`docker compose up --build -d`

The images should build and start running. Checking the logs will reveal that the database needs to be initialized.
If the RabbitMQ container is unable to start, restart with `docker compose down` and `docker compose up -d`: sometimes this container has trouble initializing in time. 

4. To initialize the database with Alembic, run the following command with Assemblage still running to build the database from the latest version of the database configuration:

`docker exec -it assemblage-coordinator-1 alembic upgrade head`

To check that the database exists, run

```
docker exec -it assemblage-db psql -U assemblage
\dt
```

and check that tables are displayed. Use `exit` to get out of the database inspector. 

5. Restart Assemblage. 

```
docker compose down
docker compose up -d
```

The program should begin collecting repositories. 

## ENVIRONMENT VARIABLES

You can use one secrets.env, or multiple separate env files, but the following shows what env variables need to be in which container 
Also, in the compose file, specify the type - Coordinator,Scraper,Builder

## ENVIRONMENT VARIABLES FOR coordinator
```
DB_HOST=assemblage-db (database container name)
POSTGRES_DATABASE=assemblage
POSTGRES_USER=assemblage
POSTGRES_PASSWORD=<password>
DB_PORT=5432
MINIO_ROOT_USER=<chosen user>
MINIO_ROOT_PASSWORD=<chosen password>
```
## ENVIRONMENT VARIABLES FOR scraper
```
GITHUB_TOKEN=<github_pat_token>
```
## ENVIRONEMNT VARIABLES FOR builder
```
SAVE_ASSEMBLY=true
MINIO_ROOT_USER=<chosen user>
MINIO_ROOT_PASSWORD=<chosen password>
```

## Environemnt Variables For MINIO
```
MINIO_ROOT_USER=<chosen user>
MINIO_ROOT_PASSWORD=<chosen password>
```

## If using a builder or scraper on a distributed host also add
```
MQ_HOST=<mq_host>
MQ_PORT=<mq_port>
```
You will also need to expose the rabbitmq port. TODO: add proper authentication


Note to future developers with builder - 
On windows, there is an issue where Windows places a lock on all the built executables, this lock will not get lifted until the python program ( ie the worker script) stops and the container is downed. This means that it cannot be moved only copied to the volume ( or s3 bucket if implemented). Therefore it will progressively take more space so may have to be perioditically stopped, cleaned, and recreated.