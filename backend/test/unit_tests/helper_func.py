'''
    Various functions for creating and configuring mocks, getting complex dummy input data,
    extracting information from mocks, etc. 
'''

import json
from unittest.mock import MagicMock, ANY
from requests import Response

# General utilities

def get_slept_time_from_args(call_args_list) -> float:
    '''
        pass in MockedTime.sleep.call_args_list, returns sum of sleep times
        call_args_list must be list of unittest.mock.call objs
    '''
    sum = 0
    for call in call_args_list:

        sum += call[0][0]

    return float(sum)

def mock_functioning_rabbitmq(MockConnection, MockChannel):
    '''
        Creates a mock for RabbitMQ connections and channels, covering just enough
        functionality to run tests (instantiation + core health checks)
        Returns mock_connection, mock_channel
    '''

    mock_connection = MagicMock()
    mock_channel = MagicMock()

    mock_connection.is_open = True
    mock_connection.is_closed = False
    mock_connection.channel = MagicMock(return_value = mock_channel)
    mock_channel.is_open = True
    mock_channel.is_closed = False
    

    MockConnection.return_value = mock_connection
    MockChannel.return_value = mock_channel

    return mock_connection, mock_channel

def mock_functioning_dbmanager(MockManager):
    '''
        Creates a mock DBManager that can only insert repos successfully.
    '''
    mock_db = MagicMock()
    MockManager.return_value = mock_db
    mock_db.insert_repos.return_value = 1
    return mock_db





## Scraper responses

# An example of a response gotten by the request in _process_repo_message
def scr_full_repo_response_tree():
    ''' Request to https://api.github.com/repos/id-Software/DOOM/git/trees/a77d '''
    mock_response = Response()
    # This is actual content of a response to DOOM's repo, except SHAs have been truncated
    mock_response._content = b'''{"sha":"a77d","url":"https://api.github.com/repos/id-Software/DOOM/git/trees/a77d","tree":[
        {"path":"LICENSE.TXT","mode":"100644","type":"blob","sha":"d60c","size":17992,"url":"https://api.github.com/repos/id-Software/DOOM/git/blobs/d60c"},
        {"path":"README.TXT","mode":"c3","size":17992,"url":"https://api.github.com/repos/id-Software/DOOM/git/blobs/d60c"},
        {"path":"README.TXT","mode":"100644","type":"blob","sha":"cc7e","size":3619,"url":"https://api.github.com/repos/id-Software/DOOM/git/blobs/cc7e"},
        {"path":"sndserv","mode":"040000","type":"tree","sha":"a50d","url":"https://api.github.com/repos/id-Software/DOOM/git/trees/a50d"}
        ],"truncated":false}'''
    mock_response.headers = {'X-GitHub-Media-Type': 'github.v3; format=json', 'X-RateLimit-Limit': '60', 'X-RateLimit-Remaining': '50', 'X-RateLimit-Reset': '1762793780', 'X-RateLimit-Resource': 'core', 'X-RateLimit-Used': '10'}
    mock_response.status_code = 200
    mock_response.reason = 'ok'
    return mock_response

def scr_full_repo_response_search():
    '''A Search API response with 3 entries (#1 is DOOM, #2 and #3 are anonymized)'''
    mock_response = Response()
    with open('test/unit_tests/workers/example_search_github.json', 'r') as file:
        info = json.load(file)
        info = json.dumps(info).encode()
        mock_response._content = info

    mock_response.headers = {'X-GitHub-Media-Type': 'github.v3; format=json', 'X-RateLimit-Limit': '60', 'X-RateLimit-Remaining': '50', 'X-RateLimit-Reset': '1762793780', 'X-RateLimit-Resource': 'core', 'X-RateLimit-Used': '10'}
    mock_response.status_code = 200
    mock_response.reason = 'ok'
    return mock_response

# The response sent when the rate limit has already been hit, and now no more new info
# is being returned
def scr_skeleton_rate_limit_response():

    mock_response = Response()
    mock_response._content = b'''{"message":"API rate limit exceeded for XXX.XXX.XXX.XX. (But here\'s the good news: Authenticated requests get a higher rate limit. Check out the documentation for more details.)",
        "documentation_url":"https://docs.github.com/rest/overview/resources-in-the-rest-api#rate-limiting"}'''
    mock_response.headers = {'X-GitHub-Media-Type': 'github.v3; format=json', 'X-RateLimit-Limit': '60', 'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1762793780', 'X-RateLimit-Resource': 'core', 'X-RateLimit-Used': '60', 'Content-Length': '280'}
    mock_response.status_code = 403
    mock_response.reason = 'rate limit exceeded'
    return mock_response

# A response given when bad credentials are provided
def scr_skeleton_bad_cred_response():

    mock_response = Response()
    mock_response._content = b'{"message": "Bad credentials", "documentation_url": "https://docs.github.com/rest", "status": "401"}'
    mock_response.headers = {'X-GitHub-Media-Type': 'github.v3; format=json', 'X-RateLimit-Limit': '60', 'X-RateLimit-Remaining': '50', 'X-RateLimit-Reset': '1762793780', 'X-RateLimit-Resource': 'core', 'X-RateLimit-Used': '10'}
    mock_response.status_code = 401
    return mock_response


def scr_skeleton_about_to_hit_limit_response():
    '''
    Returns a valid response, but with remaining rate limit of 0, indicating that the
    next request needs to wait.
    Note that the data in this response won't necessarily match the DOOM_REPO_MSG.
    '''
    mock_response = Response()
    mock_response._content = b'''{"sha":"a77...","url":"https://api.github.com/repos/id-Software/DOOM/git/trees/a77...","tree":[
        {"path":"LICENSE.TXT","mode":"100644","type":"blob","sha":"d60...","size":17992,"url":"https://api.github.com/repos/id-Software/DOOM/git/blobs/d60..."},
        {"path":"README.TXT","mode":"100644","type":"blob","sha":"cc....","size":3619,"url":"https://api.github.com/repos/id-Software/DOOM/git/blobs/cc..."}
        ],"truncated":false}'''
    mock_response.headers = {'X-GitHub-Media-Type': 'github.v3; format=json', 'X-RateLimit-Limit': '60', 'X-RateLimit-Remaining': '0', 'X-RateLimit-Reset': '1762793780', 'X-RateLimit-Resource': 'core', 'X-RateLimit-Used': '60'}
    mock_response.status_code = 200
    mock_response.reason = 'ok'
    return mock_response

def scr_skeleton_404_response():
    mock_response = Response()
    mock_response._content = b'{\r\n  "message": "Not Found",\r\n  "documentation_url": "https://docs.github.com/rest",\r\n  "status": "404"\r\n}'
    mock_response.headers = {}
    mock_response.status_code = 404
    mock_response.reason = 'Not Found'
    return mock_response



## Other scraper only funcs

def scr_doom_messagestr():
    return json.dumps(json.loads('''{"name": "DOOM", "url": "https://api.github.com/repos/id-Software/DOOM", 
    "language": "C++", "owner_id": 1395534, "description": "DOOM Open Source Release", "created_at": "2012-01-31 21:28:06", 
    "updated_at": "2024-05-24 13:18:59", "size": 149, "build_system": "others", "branch": "master"}'''))




## DBM test only funcs

def dbm_mock_functioning_sqlalchemy(MockSession, MockCreateEngine):
    
    mock_engine = MagicMock()
    MockCreateEngine.return_value = mock_engine

    mock_session = MagicMock()
    MockSession.return_value.__enter__.return_value = mock_session
    return mock_session, mock_engine



def get_queries_from_session_str(mocked_session):
    ''' Returns a list of all queries called in a session. 
        Returns list of strings
    '''
    queries = []
    for call in mocked_session.execute.call_args_list:
        query = call[0][0]  
        queries.append(get_str_from_query(query))

    return queries


def get_queries_from_session_query(MockSession):
    ''' Returns a list of all queries called in a session. 
        Returns list of queries. Good for setting up side effects
    '''
    queries = []
    for call in MockSession.execute.call_args_list:
        query = call[0][0]  
        queries.append(query)

    return queries

def get_str_from_query(query):
    
    query_with_args = str(query.compile(compile_kwargs={"literal_binds": True}))
    return query_with_args.replace('\n', '')
    