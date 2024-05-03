# Assemblage

## Deployment of popular cloud infrastructure

Assemblage is mainly tested on AWS. If you plan to use AWS services, please copy your confidential file to `$ASSEMBLAGE_HOME/aws` before system initialization, which should contains the private key and geo information

## Set up env for developing & testing


1. Create the docker network
```
docker network create assemblage-net
```

2. Build docker images
```
./build.sh
```

2. Run and initialize MySQL.
```
docker pull mysql/mysql-server
# publish port 3306 and add a volume so the data can be accessed locally.
docker run --name=mysql -v $(pwd)/db-data:/var/lib/mysql -p 3306:3306 --network=assemblage-net -d mysql/mysql-server
docker logs mysql
# find the tmp password in log
# may need to wait a minute for the database to initialize
# and the password is provided
docker exec -it mysql mysql -uroot -p


# set password to 'assemblage', change this for your own
# Make sure to set the DB password in the coordinators config
# before building the image.
mysql> ALTER USER 'root'@'localhost' IDENTIFIED BY 'assemblage';
mysql> CREATE USER 'root'@'%' IDENTIFIED BY 'assemblage';
mysql> GRANT ALL PRIVILEGES ON *.* TO 'root'@'%' WITH GRANT OPTION;
mysql> CREATE DATABASE IF NOT EXISTS assemblage;
```

3. Initialize the Database
```
# Run cli.py and wipe the database, it will clean the database and create tables
pip3 install -r requirements.txt
python3 cli.py
```


4. Run `docker-compose` to start up the services (Optional, cli.py can boot after initialization)
```
docker-compose up -d
```

5. Boot CLI

As Google changed some of the codes, you need to add the flag `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python` to boot CLI tools

```
pip3 install pyfiglet prompt_toolkit pyfiglet plotext pypager grpcio grpcio-tools
PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python python3 cli.py --server $(docker inspect --format '{{ $network := index .NetworkSettings.Networks "assemblage-net" }}{{ $network.IPAddress}}'  assemblage_coordinator_1):50052
```
