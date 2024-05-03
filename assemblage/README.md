# Assemblage AWS Deployment Instructions 

Assemblage is mainly tested on AWS. If you plan to use AWS services, please copy your confidential file to `$ASSEMBLAGE_HOME/aws` before system initialization, which should contains the private key and geo information.

To ensure the security of system, please enable firewall for instances, particularly 5672 and 50052 port of coordinator node, 3306 port of database, and other necessary ports.

## 1. Coordinator Setup

1. Create the docker network
```
docker network create assemblage-net
```

2. Build docker images, add git tokens
```
./build.sh
```

2. Run and initialize MySQL. All passwords are using default `assemblage` under user `roor`, change these if needed

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
# Run cli.py and follow the intructions, it will create tables and setup other configs
pip3 install -r requirements.txt
python3 cli.py
```


4. Use `start.sh` to restart the services if needed
```
sh start.sh
```

5. Boot CLI

As Google changed some of the codes, you need to add the flag `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python` to boot CLI tools

```
pip3 install pyfiglet prompt_toolkit pyfiglet plotext pypager grpcio grpcio-tools
PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python python3 cli.py --server $(docker inspect --format '{{ $network := index .NetworkSettings.Networks "assemblage-net" }}{{ $network.IPAddress}}'  assemblage_coordinator_1):50052
```

## Crawler Setup

A crawler is provided with the system, it need a object of data source, which is the website it crawls,the constructor for such data source is provided as `GithubRepositories`, which takes in tokens, query parameters and query time intervals, for example:

```
GithubRepositories(
    git_token="some_token_here",
    qualifier={
    }, 
    crawl_time_start=1262322000,
    crawl_time_interval=86400,
    proxies=[],
    build_sys_callback=(lambda files: return "")
)

```

It is also possible to implement crawler to other websites by extending the [`DataSource`](worker/scraper.py) class

## Workers API and Deployment

The exposed worker APIs locate in [api.py](api.py)

```
from assemblage.worker.profile import AWSProfile
from assemblage.worker.build_method import BuildStartegy, DefaultBuildStrategy
```

where the `BuildStartegy` specifies the behavior of Building process, and each abstract method represents each building stages. If you want to fully customize the building/post building behavior, provide the `clone_data`, `pre_build`, `run_build` and `post_build_hook` function with your code, the function input indicates the build configuration (you can ignore these if you pass in your own build configs)

`class BuildStartegy`: An abstract class that encapsulates the Assemblage's worker behavior

    clone_data(self, repo):
        Return clone_msg, clone_status(indicated by BuildStatus), clone_dir (the directory where is cloned to)
        The method to clone the repository to local machine

    pre_build(self, Platform,
                    Buildmode, Target_dir, Optimization, _tmp_dir, VC_Version, Favorsizeorspeed="",
                    Inlinefunctionexpansion="", Intrinsicfunctions="") -> tuple[str, int, str]:
        Return the processing message, status code, Makefile (or solution file) path
        The method process the files in Target_dir to meet other config parameters

    run_build(self, repo, target_dir, build_mode, library, optimization, slnfile,
                  platform, compiler_version) -> tuple[bytes, bytes, int]:
        Return the stderr and stdout in bytes, and status code
        The actual function that a command is called to perform the compilation and building
    
    post_build_hook(self,
                    dest_binfolder, build_mode, library, repoinfo, toolset,
                    optimization, commit_hexsha):
        No return value, this function processes the dest_binfolder and upload it


## Examples

Example workers can be found at [example_cluster.py](../example_workers/example_cluster.py), [example_windows.py](../example_workers/example_windows.py), [example_vcpkg.py](../example_workers/example_vcpkg.py).
If you don't need customization, check [stable branches](https://github.com/harp-lab/Assemblage/branches) that has been deployed and tested on AWS for months.


### Example Windows Setup

To setup the system to generate Windows PE binaries, we need to first sprcify the repositories to crawl, so on the coordinator side

```
GithubRepositories(
    git_token="some_token_here",
    qualifier={
        "language:c",
        "stars:>2",
        "license:"mit"
    }, 
    crawl_time_start= 1262322000,
    crawl_time_interval=86400,
    proxies=[],
    build_sys_callback=(lambda files:1 in [for file in files if file.lower().endswith(".sln")])
)
```

is used to create one crawler using the provided token, to crawl the repositories written in C, has more than 2 stars and has mit license. And `build_sys_callback` will take in a function, which will return the build tool from the files in this repository.

After reposotories are crawled, it will be packed to tasks with a series of build configurations, which can be defined as 

```
build_option(
    1, platform="windows", language="c++",
    compiler_name="v143",
    compiler_flag="-Od",
    build_command="Debug",
    library="x64",
    build_system="sln")   
```

where the first argument is the channel worker would listen, and all tasks with this configuration will be distributed through this channel.

Then after booting the coordinator, we need to configure the worker side, the [example_windows.py](../example_workers/example_windows.py) shows an implementation of Windows builder utilizing the msbuild.
The class `WindowsDefaultStrategy` provides the implementation of a Windows worker that, `pre_build` first parse the `vcxproj` and `sln` files, change the configurations and store the files, `run_build` call the `msbuild` with customized configurations, then `post_build_hook` parse the `Dia2Dump` output and stores all the information for future reference.
