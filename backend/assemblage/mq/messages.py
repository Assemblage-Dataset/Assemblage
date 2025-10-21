import json

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
    
    


class BuilderRegIn(MQMsg):
    '''
    Create Builder Registration RabbitMQ message
    Sent from Builder worker to Coordinator on first start up
    '''
    def __init__(self, name: str, uuid: str, compiler: str,
                 compiler_version: str, reply_to_queue: str, 
                 language: str, save_assembly):
        super().__init__()
        self.name: str = name
        self.uuid: str = uuid
        self.compiler: str = compiler
        self.compiler_version: str = compiler_version
        self.reply_to_queue: str = reply_to_queue
        self.language: str = language
        self.save_assembly: bool = save_assembly

class BuilderRegOut(MQMsg):
    '''
    Messages that the cooridnator sends to the builder worker
    '''
    def __init__(self, build_opt_queue: str):
        super().__init__()
        self.build_opt_queue: str = build_opt_queue # what build option queue to listen to for the worker
        
