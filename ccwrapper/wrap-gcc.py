#!/usr/bin/env python
'''
a python wrapper for gcc

Yihao Sun
'''

import sys
import os

args = ['gccr'] + sys.argv[1:]
print(args)
os.system(' '.join(args))
