# repo-scraper-builder

* Find C/C++ projects on GitHub
* Clone repos local machine
* Experiment with build systems and flags to get diversity of binaries
* Maintain record of repo/binary metadata throughout

Design document notes: https://docs.google.com/document/d/1p9SvTGqZT9zI8eYj6Z1dHr9NWoytWdvptI3m0Kixqo4/edit
https://docs.google.com/document/d/1pjuG1iYjtir0W1pzO09rr49sehRp5bbLsamGmyPzJw4/edit

## AWS
Assemblage support aws deployment, please copy your `.aws` folder to `{ASSEMBLAGE_HOME}/aws` before system initialization.

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
```
pip3 install pyfiglet prompt_toolkit pyfiglet plotext pypager grpcio grpcio-tools
python3 cli.py --server $(docker inspect --format '{{ $network := index .NetworkSettings.Networks "assemblage-net" }}{{ $network.IPAddress}}'  assemblage_coordinator_1):50052
```
