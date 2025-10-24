import json
import platform

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

