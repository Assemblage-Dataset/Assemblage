import os
import time
import re
import sys

sys.path.append('../assemblage')
from assemblage.protobufs.assemblage_pb2 import DumpRequest, RepoRequest, WorkerRequest, BuildRequest, \
ProgressRequest, Repo, BuildOpt, enableBuildOptRequest, getBuildOptRequest, SetOptRequest
from assemblage.protobufs.assemblage_pb2_grpc import AssemblageServiceStub
import grpc
from datetime import datetime
import time

import subprocess


def get_prgress():
    result = subprocess.run(['docker', 'inspect', 'assemblage_coordinator_1'], stdout=subprocess.PIPE)
    output = result.stdout.decode("utf-8").split("\n")
    addr = ""
    for x in output:
        if '"IPAddress":' in x:
            addr_get = x.split(":")[1]
            if addr_get!="":
                addr = addr_get
    addr = addr.replace(",","").replace('"',"").replace(" ","")

    with grpc.insecure_channel(f"{addr}:50052") as channel:
        stub = AssemblageServiceStub(channel)
        request = ProgressRequest(request='req')
        response = stub.checkProgress(request)
        return response


calibrate = 0

while True:
    try:
        response = get_prgress()
        msg = []
        msg.append(
            f"Cloned Success: {response.hour_clone}/past hr {response.day_clone}/past d {response.month_clone}/past m")
        msg.append(
            f"Clone Fails: {response.hour_fail_clone}/past hr {response.day_fail_clone}/past d {response.month_fail_clone}/past m ")
        msg.append(
            f"Build Success: {response.hour_build}/past hr {response.day_build}/past d {response.month_build}/past m")
        msg.append(
            f"Build Fails: {response.hour_fail_build}/past hr {response.day_fail_build}/past d {response.month_fail_build}/past m")
        msg.append(
            f"Binaries Saved: {response.hour_binary}/past hr {response.day_binary}/past d {response.month_binary}/past m")
        msg.append(
            f"EXE/DLL Saved: {response.hour_Windows_binary}/past hour {response.day_Windows_binary}/past day {int(response.month_Windows_binary)-calibrate} Total")
        msg = "\n".join(msg)
        os.system("clear")
        print(msg)
        time.sleep(15)
    except Exception as e:
        print(e)
        time.sleep(15)

