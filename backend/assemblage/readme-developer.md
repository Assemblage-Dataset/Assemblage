# Simple customizations

## Builder customization

The builder is composed of two parts: the build method and the builder itself. The build method is the piece of code which does the work of building on a particular platform: the builder handles common functionality such as moving files, cloning repos, and sending messages. 
If you want to add more supported platforms/languages, you'll want to define enums for these (check consts.py, search for `SupportedPlatform` to find the enums). If you want to add another platform, you'll want to extend the base `BuildStrategy` class in build_method.py, then hook your created class in to the builder (inside the builder, look for the line `self.platform == SupportedPlatform.LINUX:`). Also inside the builder, you'll want to check that `save_binaries` has defined behavior for your platform.
Finally, when creating your builders in a compose file, you'll want to ensure that they're set up for your new options. An example, taken from the Windows builder:

```
    environment:
      PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION: python
      name: custom_builder
      TYPE: builder
      LANGUAGE: "c++"
      COMPILER: "MSVC"
```

Note to future developers with builder - 
On windows, there is an issue where Windows places a lock on all the built executables, this lock will not get lifted until the python program ( ie the worker script) stops and the container is downed. This means that it cannot be moved only copied to the volume ( or s3 bucket if implemented). Therefore it will progressively take more space so may have to be perioditically stopped, cleaned, and recreated.

## Scraper customization

### Modify start and end times

The defaults (see config.py) cover a year of time: by default, the system starts scraping "now" and ends "a year ago". These can be modified. The scraper policy can be changed: see `ScraperOutputPolicy` in `consts.py` for a brief explanation of the differences. 
In `docker-compose`:
```
    ...
    scraper_0:
    image: assemblage-gcc:default
    ...
    environment:
        PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION: python
        name: scraper_0
        TYPE: scraper
        SCRAPE_START_TIME: github
        SCRAPE_END_TIME: 
        SCRAPER_POLICY: 
    ...
```

Qualifiers (e.g. restricting by language and other filters) and proxies for HTTP connections must be set directly in config.py: a good future development could be to set these up so they can also be set with environment variables. 

### Token cycling

If one wishes to use multiple tokens, for whatever reason (e.g. if one elects to use their own personal tokens in addition to an organizational token), there exists a framework for this. Replace `alternative_git_tokens` in config.py with the commented-out code as an example, and set the `BACKUP_TOKEN_1`... etc. environment variables in `secrets.env`. 

### Add datasource

As written, the system works with one scraper. However, if scraping from multiple sources is desired, one can expand the Scraper class by creating a new child method of the DataSource class (e.g. NotGithub), adding an initialization of that DataSource within the scraper, adding a corresponding enum value to `ScrapeSource` in `consts.py`, and setting the environment of that scraper to the value of that source. E.g.:
In `docker-compose`:
```
    ...
    scraper_1:
    image: assemblage-gcc:default
    ...
    environment:
        PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION: python
        name: scraper_0
        TYPE: scraper
        SCRAPE_DATASOURCE: not_github
    ...
```

In `consts.py`:

```
class ScrapeSource(str, Enum):
    ''' Used by the scraper to name a valid source of data '''
    GITHUB = "github"
    NOT_GITHUB = "not_github"
```

In `scraper.py`:

```



    def __init__(self, settings: ScraperSettings, workerid: int):
        
        super().__init__(settings.name, settings.mq_host,
                         settings.mq_port, worker_type=WorkerType.Scraper)

        elif settings.source == ScrapeSource.NOT_GITHUB:
            self.data_source = NotGithubSource(
                workerid,
                ...
                # add your code here
            )

        else:
            
            if settings.source != ScrapeSource.GITHUB:
                logger.error(f"Scrape source {settings.source} not defined.")

            self.data_source = GithubRepositories(
                workerid,
                git_token=settings.git_token,
                alternate_git_tokens=settings.alternative_git_tokens,
                
                ...
            )

```



# General overview of structure

Assemblage is a distributed system, composed of a number of workers which communicate with each other over RabbitMQ. There is one coordinator worker, which passes information between the workers; likely only one scraper, which responds to the coordinator's requests for repositories to clone and sources them; and a number of builders, which receive tasks to build from the coordinator and build them. 
On startup:
* The docker file starts up the MinIO bucket, the postgresql+psycopg2 database, and the RabbitMQ server. The PostgreSQL database can be accessed using `docker exec -it assemblage-db psql -U assemblage`: this is useful for examining the database. The RabbitMQ command line can be accessed with `docker exec -it assemblage-rabbitmq-1 sh`: this can be used to run various useful commands, such as `rabbitmqctl` and `rabbitmqctl list_queues`, which are handy for troubleshooting problems with RabbitMQ. 
* The coordinator is run. 
    * All Assemblage workers enter at the `backend/scripts/start_worker.py` function, with the individual workers branching off at the `case` statement. 
    * Like the other workers, the coordinator takes its configuration from the config.py file: differences in worker behavior are ultimately a combination of the defaults defined here and any changes made in the environment variables of each worker. 
* Unlike the builder and scrapers, the coordinator does not inherit from the BasicWorker class: it enters directly at the run() function, where it checks the database and initializes it for a new run or a restart. Then, it spins up control threads, consume threads, and dispatch threads. I will summarize these threads in the rough order that a repository will travel through them.
    * Each consume thread listens to a particular `InputQueue` (see consts.py): when a worker publishes a message on any of these queues, that message is processed by the corresponding thread. For most if not all consume threads, the messages are sent as a serialized MQMessage (see `backend/assemblage/mq/messages.py`) to ensure standardization of sent and received message formats. 
    * The consume thread on `scrape` takes the metadata of scraped repositories and places them in the `projects` table. It also creates a row or multiple rows in the `b_status` table, one for each build option (explained in two paragraphs). Notice that the message format is slightly different for these, using a 'bundle' message: this is essentially a serialized list of single scraper messages, and serves to reduce the number of messages sent and received. This is the first stage in building a repository: it has neither been cloned nor built at this point. 
    * A dispatch thread exists for each build option. A build option is, essentially, a configuration or platform on which to compile: each builder has exactly one build option, but multiple builders can have the same build option. 
    * One dispatch thread monitors the `projects` table. When the queue that the dispatch thread feeds to is nearly empty, this thread will pick a project from the `projects` table, given that the project has not yet been dispatched by this thread. The thread then dispatches this project by sending a message. This message will be picked up by the builder. In this way, a single project will have as many tasks as there are build options: perhaps one task to build a Windows build, one to build on Linux using clang, and another to build on Linux using gcc. These tasks are created when the project is received on the consume thread `scrape`, or when a new builder is put online. Much of the logic is contained in `get_dispatch_task` in `assemblage/data/db.py` (yes, there are three similarly named folders. `data` is the relevant one.)
    * Each builder monitors its own `build_opt` queue for new tasks. 
        * As an extension of BasicWorker, each builder technically has two queues that it monitors in separate threads. When a message appears on `self.control_queue_in`, the builder calls `control_message_handler` with the content of the message stored in `body`. Likewise, when a message appears on `self.build_opt_queue`, the builder calls `job_handler`. The functions `run_ctrl` and `run_job` do the work of listening to these queues, and shouldn't need to be modified significantly. In the builder, the control thread is used only for initial registration, whereas in the scraper, both queues are used throughout the lifetime of the worker. 
    * When the builder receives a new task, it begins the task of cloning, uploading, prebuilding, compiling, and postprocessing the task. The builder sends various messages on RabbitMQ updating the coordinator on its progress: these messages are received by the coordinator threads of `recv_clone`, `recv_binary` and finally `recv_build_info`. Each of these functions updates the database to reflect the current progress of the builder. The builder does the work of moving S3 buckets.
    * A build command is generated using the build method of the builder: see the `run_build` function. After the build is completed, the build method reports to the builder on success or failure. 

After the build has been completed, the execution of a single build is complete.


### RabbitMQ wrapper

In `backend\assemblage\mq\client.py` is the custom RabbitMQ wrapper written to hopefully simplify the process of using RabbitMQ, a thread-unsafe package, in a multithreaded program. 
Each thread must have its own Connection: multiple threads cannot share a Connection. However, the same queue can be accessed from any Connection. A MQQueue object represents a single queue on the RabbitMQ server: the most important parts of this are the name (as listed in `rabbitmqctl list_queues`), the exchange name (used by the builders -- see RabbitMQ routing) and the routing key (used by builders). 
Each Connection needs a Channel created inside of it: this can be done by calling `create_channel` on the Connection object returned by `create_connection`. 


### Future improvements

Below are some ideas for future improvement of the system. 

On windows, there is an issue where Windows places a lock on all the built executables, this lock will not get lifted until the python program ( ie the worker script) stops and the container is downed. This means that it cannot be moved only copied to the volume ( or s3 bucket if implemented). Therefore it will progressively take more space so may have to be periodically stopped, cleaned, and recreated. To do this, every now and then, you should stop and remove the container. The repositories are cloned to C:/temp folder first if in s3 move, and so removing the container should automatically remove them. If it doesn't, or you are running in nons3 mode then leave the container running, exec into it, and manually remove them. Once the container has been stopped, the lock should be removed, so any extant binaries should be removable at this point. 



Also suggested improvements: The commit hash is now sent with the scraper, so we would recommend sending that to the builder instead of using more subprocess commands to extract it. Rabbitmq is currently unsecured and uses the default credentials. Either setup firewall rules to ensure only the distributed builder can access the server, or implement security using the RabbitMQ access control guide [here](https://www.rabbitmq.com/docs/access-control). Adding a reverse proxy should also be on the list, to allow https connections to minio. There are also no security policies included with the minio buckets, that should be in a production environment


There are a fair number of unnecessary/potentially unnecessary files that could be removed: for example, there are three separate db.py files, each in its old folder, and I believe some of these could be removed. In addition, both of the cloners seem to be unused, as well as a fair number of the auxiliary functions (most of clang_parser seems unused, for example, and I think that profile.py is now unused). In general, the organization and redundancy of the code could both use some work.


Setting up the FastAPI server/website: the beginning of this project is present in the `assemblage/api` folder. The website would, ideally, allow a user without detailed knowledge of Assemblage's workings to view and download binaries, perhaps sorting or filtering binaries, monitoring for issues, and modifying some settings (such as changing the type of repositories collected, altering build settings, and adding or removing builders on the fly).

Getting the disassembler, postprocessor, and vcpkg modules working again

