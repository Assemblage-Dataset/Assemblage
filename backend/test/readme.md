Tests are a work in progress. The goal is good unit test coverage (ideally for everything), plus integration tests
ensuring that all parts of the program work together as expected. 

## Running tests

Assuming you've gotten the main system up and running, you can run tests with 
```
docker compose -f docker-compose-tests.yml up
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
python -m unittest discover -s test -p '*_test.py'
```
As of right now, there shouldn't be direct conflicts if you try to run the
tests at the same time as the main compose file, since RabbitMQ and the database are dummied out, but I don't recommend this for two reasons:
* If there's a mistake somewhere in dummying out a resource, then test messages or files might end up not removed, or processed by the main system.
* In the future I or someone else may use resources shared between containers (i.e. persistent files, database, rabbitmq) for integration tests, in which case the same problem as above.

Tests are a little inconsistent in whether they use external dependencies or not: currently, I try to mock out any references to 
RabbitMQ or the database, but have kept in the GitHub REST API. This means that the tests that use requests (currently only the scraper)
MAY fail despite working correctly if the repositories that I used as a benchmark change, which is bad. On the other hand, that means
that these tests will also fail if the GitHub API changes, which is good, because that means we'll need to change our code. 

## Adding and changing tests

All test files are named "module"_test.py, and this is the format that the docker compose file looks for tests in.
It also only runs tests that are located in the test/ folder, which is currently contained in backend.
If you want to change the test organization structure, you can, but know you'll probably also have to change
the compose file. 
Coverage is certainly not complete, and tests as written focus on either expected program states or frequent causes of errors.
