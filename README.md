# Assemblage

Assemblage is a distributed binary corpus discovery, generation, and archival tool built to provide high-quality labeled metadata for the purposes of building training data for machine learning applications of binary analysis and other applications (static / dynamic analysis, reverse engineering, etc...). You can find our paper on [arxiv](https://arxiv.org/abs/2405.03991). A brief introduction to the APIs and deployment can be found [here](https://assemblagedocs.readthedocs.io/). Quickstart instructions can be found further down on  this README. 

<i>The code in this repository is published under the MIT license.</i>

## Dataset Availability

For up to date info and to download the dataset, please visit the [dataset page](https://assemblagedocs.readthedocs.io/en/latest/dataset.html).

We include __**only**__ the subset of binaries for which permissive licenses can be ascertained. For more information, please view our [data sheet](https://assemblage-dataset.net/assets/total-datasheet.pdf). 

# Quickstart

## Initial Setup (Linux Build)

1. Clone the repo and install Docker. Optionally, create and configure a GitHub token. 

2. Within the project directory, create a secrets.env file with the following environment variables:

    ```
    DB_HOST=assemblage-db
    DB_PORT=5432
    POSTGRES_DATABASE=assemblage
    POSTGRES_USER=assemblage
    POSTGRES_PASSWORD=<password>
    GITHUB_TOKEN=<token>
    ```

3. Run Docker, then run the following command in the Assemblage root directory to build and run the Docker images. This will take some time. 

    `docker compose up --build -d`

The images should build and start running, but will not produce any artifacts. Checking the logs will reveal that the database needs to be initialized with Alembic.

<i>If the RabbitMQ container is unable to start, restart (see step 5): sometimes, this container has trouble initializing in time. </i>

4. To initialize the database with Alembic, run the following command (with Assemblage still running) to build the database from the latest version of the database configuration:

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

## Utilizing S3 Bucket (Recommended)

If you plan on using MinIO, for local S3 bucket integration, you will need to select a username and password for the MinIO console. Define them in the secrets.env file with the following environment variables:

```
S3_ACCESS_KEY=<chosen S3 username>
S3_SECRET_ACCESS_KEY=<chosen S3 password>
MINIO_ROOT_USER=<chosen S3 username>
MINIO_ROOT_PASSWORD=<chosen S3 password>
```

Then, add the following required environment variables:
```
S3_HOST=minio
S3_HTTPS=false
```

Assemblage must then be launched with a MinIO bucket running. An example deployment configuration can be found in `docker-compose-s3.yml`.

Files will no longer be stored in the local binaries folder, and instead can be accessed via the MinIO interface. To access this interface, start Assemblage, view the logs of the `minio` container, and look for the links labelled "API" and "WebUI". Log in with the credentials defined in secrets.env. Further resources can be found in the MinIO documentation. 

## Distributed Builders / Windows Builders (Optional)


When building Windows executables, unlike the other workers, the builder must run a Windows image: in order to do this, a Windows kernel must be available to the builder. Due to the restrictions Docker places on running containers with mixed or non-Linux kernels, this typically requires a builder on a separate Windows machine to be connected to the rest of the system. 


To configure a remote builder:
1. First, ensure that on the local host (the server running the coordinator), the RabbitMQ ports are exposed and open to other connections. If using MinIO, likewise ensure that relevant ports are exposed. The default ports of these services can be looked up in their respective documentation: alternately, the provided `docker-compose-s3` can be used for the local host, and lists the ports of both services. 

<i>NOTE. Running distributed builders requires the RabbitMQ server to be exposed. Currently, the default username/passwords are used, so we recommend that you set up firewall rules to ensure only your worker host(s) can access the RabbitMQ server.</i>

2. On your remote host, use a new docker compose file that only contains a builder. See `docker-compose-windows.yml` for reference.
3. Modify the remote host's environment variables: set the `S3_HOST` and the `MQ_HOST` address to be the IP  or DNS name (if set) of your main host. If you have enabled https on the S3 host, then make sure to set `S3_HTTPS = true` on the remote host as well. 
4. If you are using a non-standard port for RabbitMQ and/or S3, then you must additionally set `S3_PORT` and `MQ_PORT`.
5. Start the main Assemblage system on the local host, then the remote host. 

## Troubleshooting

Often small errors can be fixed by restarting or rebuilding the Docker containers: this is particularly true for fresh installs or configuration changes. Otherwise, setting the `RUNTIME_ENV` environment variable to `development` will expose more logs, which may be handy for troubleshooting. 

The repository contains a suite of unit and integration tests, which may be useful for those looking to expand on Assemblage. Further information can be found in the [README file](backend/test/readme.md) located within the test folder.

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