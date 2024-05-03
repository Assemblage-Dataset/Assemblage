#!/usr/bin/env python
'''
a python wrapper for clang -O1

Yihao Sun
'''

import sys
import os

args = ['clangr', '-O1']
for arg in sys.argv[1:]:
    if arg.startswith('-O'):
        continue
    args.append(arg)
print(args)
os.system(' '.join(args))
