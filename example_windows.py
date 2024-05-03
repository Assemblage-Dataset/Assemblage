
import glob
import json
import logging
import os
import time

from assemblage.bootstrap import AssmeblageCluster
from assemblage.worker.scraper import GithubRepositories
from assemblage.worker.profile import AWSProfile
from assemblage.worker.postprocess import PostAnalysis
from assemblage.worker.build_method import BuildStartegy, DefaultBuildStrategy

time_now = int(time.time())
start = time_now - time_now % 86400
querylap = 1440000
aws_profile = AWSProfile("assemblage-test", "assemblage")

def get_build_system(_files):
    """Analyze build tool from file list"""
    return "sln"

a_crawler = GithubRepositories(
    git_token="",
    qualifier={
        "language:c++",
        "topic:windows",
    }, 
    crawl_time_start= start,
    crawl_time_interval=querylap,
    crawl_time_lap=querylap,
    proxies=[],
    build_sys_callback=get_build_system
)

another_crawler = GithubRepositories(
    git_token="",
    qualifier={
        "language:c++",
        "topic:windows",
        # "stars:>10"
    }, 
    crawl_time_start= start,
    crawl_time_interval=querylap,
    crawl_time_lap=querylap,
    proxies=[],
    build_sys_callback=get_build_system
    # sort="stars", order="desc"
)

test_cluster_windows = AssmeblageCluster(name="test"). \
                build_system_analyzer(get_build_system). \
                aws(aws_profile). \
                message_broker(mq_addr="rabbitmq", mq_port=5672). \
                mysql(). \
                build_option(
                    100, platform="windows", language="c++",
                    compiler_name="v143",
                    compiler_flag="-Od",
                    build_command="Debug",
                    library="x64",
                    build_system="sln"). \
                builder(
                    "windows", "msvc", 100, docker_image="",
                    custom_build_method=DefaultBuildStrategy(),
                    aws_profile= aws_profile
                ). \
                scraper([a_crawler, another_crawler]). \
                use_new_mysql_local()


test_cluster_windows.boot()
