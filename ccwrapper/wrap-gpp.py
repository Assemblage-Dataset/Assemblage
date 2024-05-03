#!/usr/bin/env python
'''
a python wrapper for clang

Yihao Sun
'''

import sys
import os

args = ['g++r'] + sys.argv[1:]
print(args)
os.system(' '.join(args))
