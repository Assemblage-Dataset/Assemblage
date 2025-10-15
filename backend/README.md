## Assemblage Backend

Python - consists of the workers + the eventual fastapi backend

Note - the uvloop dep is for the fastapi server, but is incompatible for windows, hence the conditional install only if not Windows.


You cannot run both windows and Unix containers on same host, so have to pick one. 