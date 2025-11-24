import json
import platform
import time

class MQMsg:
    def __init__(self):
        pass
    @classmethod
    def from_json(cls, json_str: str):
        '''
        Create Build Registration
        '''
        data = json.loads(json_str)
        return cls(**data)
    
    def to_json(self) -> str:
        '''
        Create JSON string for rabbit mq to send
        '''
        return json.dumps(self.__dict__)

    def __str__(self):
        '''
        Maybe do a better print?
        '''
        return f'{type(self)}:{self.to_json()}'
    
    


class BuilderRegIn(MQMsg):
    '''
    Create Builder Registration RabbitMQ message
    Sent from Builder worker to Coordinator on first start up
    '''
    def __init__(self, name: str, uuid: str, compiler: str,
                 compiler_version: str, library: str,
                 language: str, save_assembly: bool, platform: str, compiler_flag: str, build_command: str, build_system: str):
        super().__init__()
        self.name: str = name
        self.uuid: str = uuid
        self.compiler: str = compiler
        self.compiler_version: str = compiler_version
        self.library = library
        self.language: str = language
        self.save_assembly: bool = save_assembly
        self.platform: str = platform
        self.compiler_flag = compiler_flag
        self.build_command: str = build_command
        self.build_system: str = build_system

class BuilderRegOut(MQMsg):
    '''
    Messages that the cooridnator sends to the builder worker
    '''
    def __init__(self, build_opt_id: int, build_opt_queue: str | None = None):
        super().__init__()
        self.build_opt_id: int = build_opt_id
        self.build_opt_queue: str = build_opt_queue if build_opt_queue else f"build_opt_{build_opt_id}"  # what build option queue to listen to for the worker



class ScraperDataOutSingle(MQMsg):
    '''
    Format of a single repository message.
    By default, the scraper sends these in bundles of 10 (see ScraperDataOutBundle)
    '''
    def __init__(self, name: str, url: str, language: str,
                 owner_id: int, description: str,
                 created_at: str, updated_at: str, size: int, 
                 build_system: str, branch: str):
        super().__init__()
        self.name: str = name
        self.url: str = url
        self.language: str = language
        self.owner_id: int = int(owner_id)
        self.description: str = description
        self.created_at: str = created_at
        self.updated_at: str = updated_at
        self.size: int = int(size)
        self.build_system: str = build_system
        self.branch: str = branch

    def to_dict(self):
        return self.__dict__

class ScraperDataOutBundle(MQMsg):
    '''
        Represents an array of ScraperDataOutSingle (as dicts). Sent from scraper to coordinator
    '''
    def __init__(self, repo_array=[]): # type of repo_array should be ScraperDataOutSingle[]
        super().__init__()
        self.repos = repo_array

    def to_json(self):
        # returns a json that converts to an array of dictionaries
        return json.dumps(
            [r.to_dict() for r in self.repos]
        )
    
    @classmethod
    def from_json(cls, json_str : str): # the json_str should represent a list of dictionaries
        # Creates a new ScraperDataOutSingle for each dict in the json str
        body = json.loads(json_str)
        return cls(
            [ScraperDataOutSingle(**r) for r in body]
        )
    
    def __iter__(self):  # iterate over self data
        for r in self.repos:
            yield r

    def __str__(self):
        return f"ScraperDataOutBundle({len(self.repos)})"
    
    def __len__(self):
        return len(self.repos)

class BuildCloneReq(MQMsg):
    
    def __init__(self, uncloned_repo, repo_url, task, build_opt, reproduce_mode=False):
        super().__init__()
        self.name = uncloned_repo.name
        self.url = repo_url
        self.task_id = task.id
        self.opt_id = build_opt.id
        self.commit_hexsha = task.commit_hexsha or None
        self.repo_id = uncloned_repo.id
        self.updated_at = uncloned_repo.updated_at.strftime("%m/%d/%Y, %H:%M:%S")
        self.build_system = uncloned_repo.build_system
        self.msg_time = time.time()
        
        if reproduce_mode:
            self.mod_timestamp = task.mod_timestamp