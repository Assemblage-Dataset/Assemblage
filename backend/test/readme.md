Tests are a work in progress. The goal is good unit test coverage (ideally for everything), plus integration tests
ensuring that all parts of the program work together as expected. 

## TL;DR:

* run unit tests with `docker compose -f docker-compose-unittests.yml up`: no further setup necessary
  * modify `docker-compose-unittests.yml` to run particular unit tests, or a particular subset of unit tests
* run integration tests with `docker compose -f docker-compose-integrationtests.yml up` after setting up the test database
  * the relevant commands, to be run in the `tests` container, are 
  `command: python /app/test/integration_tests/setup.py` and then 
  `command: python -m unittest discover -s test/integration_tests -p '*_test.py'`
  * use the command `command: python /app/test/integration_tests/db_manager_test.py TestDBManager.setup_helper` to put the container in an infinite loop and keep it running (useful for detached)
  * the database can be examined with `docker exec -it assemblage-test-db psql -U assemblage` when the container is running in detached mode


## Running unit tests

Assuming you've gotten the main system up and running, you can run unit tests with simply 
```
docker compose -f docker-compose-unittests.yml up
```
In the Compose file, you can run a particular test with the command
```
python /app/test/path/to/"module"_test.py "TestModuleName"."test_name"
```
e.g.
```
python /app/test/unit_tests/workers/coordinator_test.py TestCoordinator.test_recv_build_info_clone_wait_eventual_success
```
or run all tests with the command
```
python -m unittest discover -s test/unit_tests -p '*_test.py'
```
If you discover inside of the full test folder instead of looking inside the unit_tests subfolder, you'll also run the integration tests, which you don't want and won't work, because the integration tests depend on test services that aren't created for the unit tests. 
As of right now, there shouldn't be direct conflicts if you try to run the
tests at the same time as the main compose file, since RabbitMQ and the database are dummied out, but I don't recommend this for two reasons:
* If there's a mistake somewhere in dummying out a resource, then test messages or files might end up not removed, or processed by the main system.
* In the future I or someone else may use resources shared between containers (i.e. persistent files, database, rabbitmq) for integration tests, in which case the same problem as above.

Tests are a little inconsistent in whether they use external dependencies or not: currently, I try to mock out any references to 
RabbitMQ or the database, but have kept a few refs to the GitHub REST API. This means that the tests that use requests (currently only the scraper)
MAY fail despite working correctly if the repositories that I used as a benchmark change, which is bad. On the other hand, that means
that these tests will also fail if the GitHub API changes, which is good, because that means we'll need to change our code. 

## Running integration tests

Integration tests are a little more complex because you also need to set up the test database. There's a setup script to help with setting up the database. Set command of 'tests' container in docker-compose-integrationtests.yml to:
```
python /app/test/integration_tests/setup.py
```
Run, ascertain that the output is OK, then change the command to your desired tests, e.g. if you want to run all integration tests use:
```
python -m unittest discover -s test/integration_tests -p '*_test.py'
```

### Old instructions (if the above doesn't work)

The test database, unlike the regular database, is located in container assemblage-test-db. You can set up the database the same way that you set up the main database, except
you'll need to change DB-HOST in the environment variables to 'assemblage-test-db' in order to run alembic upgrade head and make tables.

1. in secrets.env set DB-HOST to: assemblage-test-db
2. in docker-compose-integrationtests.yml , under the 'tests' container set the command to:
```
    python /app/test/integration_tests/db_manager_test.py TestDBManager.setup_helper
```
3. in the command line run: 
```
docker compose -f docker-compose-integrationtests.yml up --detach
```
4. then after the docker container starts run:
```
docker exec -it assemblage-tests-1 alembic upgrade head
```
5. check that the test db has all the necessary tables:
```
docker exec -it assemblage-test-db psql -U assemblage
\dt
```
6. docker compose down the container, revert DB-HOST in secrets.env to the normal value, change the command back to whatever tests you want to run

## Adding and changing tests

All test files are named "module"_test.py, and this is the format that the docker compose file looks for tests in.
It also only runs tests that are located in the test/ folder, which is currently contained in backend.
If you want to change the test organization structure, you can, but know you'll probably also have to change
the compose file. 
Coverage is certainly not complete, and tests as written focus on either expected program states or frequent causes of errors.
