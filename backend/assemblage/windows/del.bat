@echo off
set INTERVAL=600
:loop
del /f/q/s assemblage\Builds
del /f/q/s assemblage\Binaries
Set-ExecutionPolicy RemoteSigned -Scope CurrentUser
timeout %INTERVAL%
goto:loop