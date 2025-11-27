'''
    [Stub] Some basic regression + unit tests for builder worker + build_method. 
    todo:
    * test cmd exec and clean folders
    * test save binaries
    * test linux build strategy
    * test windows build strategy
    * test builder job handler
    * test builder send msg
    * test run threads?
'''

import unittest
from unittest.mock import patch, MagicMock
import logging
from assemblage.consts import TEST_MESSAGE_LEVEL

logging.basicConfig(format="%(asctime)s [TEST] %(levelname)s: %(message)s", datefmt="%Y-%m-%d %H:%M:%S", level=TEST_MESSAGE_LEVEL)

logger = logging.getLogger(__name__)

class TestBuilder(unittest.TestCase):

    @unittest.skip("Builder tests not implemented")
    def test_stub(self):
        self.assertTrue(True)

    
if __name__ == '__main__':
    logger.info("Starting tests in builder_test.py")
    unittest.main()
