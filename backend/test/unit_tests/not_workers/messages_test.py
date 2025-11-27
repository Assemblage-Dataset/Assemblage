'''
    [Stub] Tests that messages work as expected (serialize/deserialize appropriately etc)
    todo
'''

import unittest
#from unittest.mock import patch, MagicMock
import logging
import assemblage.mq.messages as msg
from assemblage.consts import TEST_MESSAGE_LEVEL

logging.basicConfig(format="%(asctime)s [TEST] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S", level=TEST_MESSAGE_LEVEL)

logger = logging.getLogger(__name__)

class TestMessage(unittest.TestCase):

    # Test builder registration messages

    def test_BuilderRegIn_to_json(self):
        input = msg.BuilderRegIn(
            'NAME', 'UUID', 'COMPILER', 'COMPILER_VERS',
            'LIBRARY', 'LANGUAGE', 'SAVE_ASSEMBLY',
            'PLATFORM', 'COMPILER_FLAG', 'BUILD_COMMAND',
            'BUILD_SYSTEM'
        )
        expected = '{"name": "NAME", "uuid": "UUID", "compiler": "COMPILER", "compiler_version": "COMPILER_VERS", "library": "LIBRARY", "language": "LANGUAGE", "save_assembly": "SAVE_ASSEMBLY", "platform": "PLATFORM", "compiler_flag": "COMPILER_FLAG", "build_command": "BUILD_COMMAND", "build_system": "BUILD_SYSTEM"}'
        
        actual_out = input.to_json()
        
        self.assertEqual(actual_out, expected)


    def test_BuilderRegIn_from_json(self):
        input = '{"name": "NAME", "uuid": "UUID", "compiler": "COMPILER", "compiler_version": "COMPILER_VERS", "library": "LIBRARY", "language": "LANGUAGE", "save_assembly": "SAVE_ASSEMBLY", "platform": "PLATFORM", "compiler_flag": "COMPILER_FLAG", "build_command": "BUILD_COMMAND", "build_system": "BUILD_SYSTEM"}'
        expected = msg.BuilderRegIn(
            'NAME', 'UUID', 'COMPILER', 'COMPILER_VERS',
            'LIBRARY', 'LANGUAGE', 'SAVE_ASSEMBLY',
            'PLATFORM', 'COMPILER_FLAG', 'BUILD_COMMAND',
            'BUILD_SYSTEM'
        )

        actual_out = msg.BuilderRegIn.from_json(input)
        
        # must compare dicts because the two objects are different in memory,
        # we just want to ensure that all data is the same
        self.assertEqual(type(actual_out), type(expected))
        self.assertEqual(actual_out.__dict__, expected.__dict__)


    def test_BuilderRegOut_to_json(self):
        input = msg.BuilderRegOut(
            'BUILD_OPT_ID', 'BUILD_OPT_QUEUE'
        )
        expected = '{"build_opt_id": "BUILD_OPT_ID", "build_opt_queue": "BUILD_OPT_QUEUE"}'
        
        actual_out = input.to_json()
        
        self.assertEqual(actual_out, expected)

    def test_BuilderRegOut_to_json_queue_not_given(self):
        input = msg.BuilderRegOut(
            '99'
        )
        expected = '{"build_opt_id": "99", "build_opt_queue": "build_opt_99"}'
        
        actual_out = input.to_json()
        
        self.assertEqual(actual_out, expected)


    def test_BuilderRegOut_from_json(self):
        input = '{"build_opt_id": "BUILD_OPT_ID", "build_opt_queue": "BUILD_OPT_QUEUE"}'
        expected = msg.BuilderRegOut(
            'BUILD_OPT_ID', 'BUILD_OPT_QUEUE'
        )
        
        actual_out = msg.BuilderRegOut.from_json(input)
        
        self.assertEqual(type(actual_out), type(expected))
        self.assertEqual(actual_out.__dict__, expected.__dict__)

    # Test scraper messages

    def test_ScraperDataOutSingle_to_json(self):
        input = msg.ScraperDataOutSingle(
            'NAME', 'URL', 'LANG', 1, 'DESCRIPTION',
            'CREATED_AT', 'UPDATED_AT', 5, 'BUILD_SYS', 'BRANCH'
        )
        expected = '{"name": "NAME", "url": "URL", "language": "LANG", "owner_id": 1, "description": "DESCRIPTION", "created_at": "CREATED_AT", "updated_at": "UPDATED_AT", "size": 5, "build_system": "BUILD_SYS", "branch": "BRANCH"}'
        
        actual_out = input.to_json()

        self.assertEqual(actual_out, expected)


    def test_ScraperDataOutSingle_from_json(self):
        input = '{"name": "NAME", "url": "URL", "language": "LANG", "owner_id": 1, "description": "DESCRIPTION", "created_at": "CREATED_AT", "updated_at": "UPDATED_AT", "size": 5, "build_system": "BUILD_SYS", "branch": "BRANCH"}'
        expected = msg.ScraperDataOutSingle(
            'NAME', 'URL', 'LANG', 1, 'DESCRIPTION',
            'CREATED_AT', 'UPDATED_AT', 5, 'BUILD_SYS', 'BRANCH'
        )
        
        actual_out = msg.ScraperDataOutSingle.from_json(input)

        self.assertEqual(type(actual_out), type(expected))
        self.assertEqual(actual_out.__dict__, expected.__dict__)


    def test_ScraperDataOutSingle_to_dict(self):
        input = msg.ScraperDataOutSingle(
            'NAME', 'URL', 'LANG', 1, 'DESCRIPTION',
            'CREATED_AT', 'UPDATED_AT', 5, 'BUILD_SYS', 'BRANCH'
        )
        expected = {"name": "NAME", "url": "URL", "language": "LANG", "owner_id": 1, "description": "DESCRIPTION", "created_at": "CREATED_AT", "updated_at": "UPDATED_AT", "size": 5, "build_system": "BUILD_SYS", "branch": "BRANCH"}

        
        actual_out = input.to_dict()

        self.assertEqual(type(actual_out), type(expected))
        self.assertEqual(actual_out, expected)


    def test_ScraperDataOutSingle_create_with_invalid_owner(self):
        '''
            Tests that a message can't be created with bad data
        '''
        with self.assertRaises(ValueError):
            # int() can also throw a typeerror, but that is NOT an expected exception
            msg.ScraperDataOutSingle(
                'NAME', 'URL', 'LANG', "INVALID_VALUE_FOR_OWNERID", 'DESCRIPTION',
                'CREATED_AT', 'UPDATED_AT', 5, 'BUILD_SYS', 'BRANCH'
            )
            
    def test_ScraperDataOutSingle_create_with_invalid_size(self):
        '''
            Tests that a message can't be created with bad data
        '''
        with self.assertRaises(ValueError):
            msg.ScraperDataOutSingle(
                'NAME', 'URL', 'LANG', 555, 'DESCRIPTION',
                'CREATED_AT', 'UPDATED_AT', "INVALID_SIZE", 'BUILD_SYS', 'BRANCH'
            )

    def test_ScraperDataOutSingle_create_with_none_size(self):
        '''
            Tests that a message can't be created with bad data
        '''
        with self.assertRaises(TypeError):
            msg.ScraperDataOutSingle(
                'NAME', 'URL', 'LANG', None, 'DESCRIPTION',
                'CREATED_AT', 'UPDATED_AT', "INVALID_SIZE", 'BUILD_SYS', 'BRANCH'
            )

    def test_ScraperDataOutBundle_to_json_onemsg(self):
        testmsg = msg.ScraperDataOutSingle(
            'NAME', 'URL', 'LANG', 1, 'DESCRIPTION',
            'CREATED_AT', 'UPDATED_AT', 5, 'BUILD_SYS', 'BRANCH'
        )
        ex_repoarray = [testmsg]
        bundle = msg.ScraperDataOutBundle(ex_repoarray)
        expected = '[{"name": "NAME", "url": "URL", "language": "LANG", "owner_id": 1, "description": "DESCRIPTION", "created_at": "CREATED_AT", "updated_at": "UPDATED_AT", "size": 5, "build_system": "BUILD_SYS", "branch": "BRANCH"}]'
        
        actual_out = bundle.to_json()

        self.assertEqual(actual_out, expected)


    def test_ScraperDataOutBundle_to_json_multimsg(self):
        '''
            Test that bundling (i.e. to send to rabbitmq) 
            works properly and saves all messages
        '''
        testmsg1 = msg.ScraperDataOutSingle(
            'NAME1', 'URL', 'LANG', 1, 'DESCRIPTION',
            'CREATED_AT', 'UPDATED_AT', 5, 'BUILD_SYS', 'BRANCH'
        )
        testmsg2 = msg.ScraperDataOutSingle(
            'NAME2', 'URL', 'LANG', 1, 'DESCRIPTION',
            'CREATED_AT', 'UPDATED_AT', 5, 'BUILD_SYS', 'BRANCH'
        )
        ex_repoarray = [testmsg1, testmsg2]
        bundle = msg.ScraperDataOutBundle(ex_repoarray)
        expected = '[{"name": "NAME1", "url": "URL", "language": "LANG", "owner_id": 1, "description": "DESCRIPTION", "created_at": "CREATED_AT", "updated_at": "UPDATED_AT", "size": 5, "build_system": "BUILD_SYS", "branch": "BRANCH"}' \
                   ', {"name": "NAME2", "url": "URL", "language": "LANG", "owner_id": 1, "description": "DESCRIPTION", "created_at": "CREATED_AT", "updated_at": "UPDATED_AT", "size": 5, "build_system": "BUILD_SYS", "branch": "BRANCH"}]'
        
        actual_out = bundle.to_json()

        self.assertEqual(actual_out, expected)


    def test_ScraperDataOutBundle_from_json_onemsg(self):
        '''
            Test that unbundling (i.e. after receiving on rabbitmq) 
            works properly, the single message is generated properly
        '''
        input = '[{"name": "NAME", "url": "URL", "language": "LANG", "owner_id": 1, "description": "DESCRIPTION", "created_at": "CREATED_AT", "updated_at": "UPDATED_AT", "size": 5, "build_system": "BUILD_SYS", "branch": "BRANCH"}]'
        
        # expected outputs:
        expected_msg = msg.ScraperDataOutSingle(
            'NAME', 'URL', 'LANG', 1, 'DESCRIPTION',
            'CREATED_AT', 'UPDATED_AT', 5, 'BUILD_SYS', 'BRANCH'
        )
        expected_repoarray = [expected_msg]
        
        actual_out = msg.ScraperDataOutBundle.from_json(input)

        self.assertEqual(type(actual_out), msg.ScraperDataOutBundle)
        self.assertEqual(type(actual_out.repos), type(expected_repoarray))
        self.assertEqual(len(actual_out.repos), 1)
        self.assertEqual(type(actual_out.repos[0]), type(expected_msg))
        self.assertEqual(actual_out.repos[0].__dict__, expected_msg.__dict__)



    def test_ScraperDataOutBundle_from_json_multimsg(self):
        '''
            Test that unbundling (i.e. after receiving on rabbitmq) works
            properly with multiple messages in bundle
        '''
        input = '[{"name": "NAME1", "url": "URL", "language": "LANG", "owner_id": 1, "description": "DESCRIPTION", "created_at": "CREATED_AT", "updated_at": "UPDATED_AT", "size": 5, "build_system": "BUILD_SYS", "branch": "BRANCH"}, {"name": "NAME2", "url": "URL", "language": "LANG", "owner_id": 1, "description": "DESCRIPTION", "created_at": "CREATED_AT", "updated_at": "UPDATED_AT", "size": 5, "build_system": "BUILD_SYS", "branch": "BRANCH"}]'
        
        # expected outputs:
        testmsg1 = msg.ScraperDataOutSingle(
            'NAME1', 'URL', 'LANG', 1, 'DESCRIPTION',
            'CREATED_AT', 'UPDATED_AT', 5, 'BUILD_SYS', 'BRANCH'
        )
        testmsg2 = msg.ScraperDataOutSingle(
            'NAME2', 'URL', 'LANG', 1, 'DESCRIPTION',
            'CREATED_AT', 'UPDATED_AT', 5, 'BUILD_SYS', 'BRANCH'
        )
        expected_repoarray = [testmsg1, testmsg2]
        
        actual_out = msg.ScraperDataOutBundle.from_json(input)

        self.assertEqual(type(actual_out), msg.ScraperDataOutBundle)
        self.assertEqual(type(actual_out.repos), type(expected_repoarray))
        self.assertEqual(len(actual_out.repos), 2)
        self.assertEqual(type(actual_out.repos[0]), msg.ScraperDataOutSingle)
        self.assertEqual(type(actual_out.repos[1]), msg.ScraperDataOutSingle)
        # not sure on these last two assertions -- do we care about order? probably doesnt hurt
        self.assertEqual(actual_out.repos[0].__dict__, testmsg1.__dict__)
        self.assertEqual(actual_out.repos[1].__dict__, testmsg2.__dict__)


    def test_BuilderTaskOut(self):
        '''
            checks both to and from json
        '''

        input = msg.BuilderTaskOut(
            name="name", url="url", task_id=50, opt_id=1, output_dir="none", repo_id=1,
            updated_at="str", build_system="str", msg_time=0.0, commit_hexsha="495"
        )
        output = input.to_json()
        output = msg.BuilderTaskOut.from_json( output )
        
        self.assertEqual(output.__dict__, input.__dict__)


    
if __name__ == '__main__':
    logger.info("Starting tests in messages_test.py")
    unittest.main()
