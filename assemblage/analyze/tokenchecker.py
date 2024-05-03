"""
Check the github API status of token

"""

import json
import time
import requests
import logging

from assemblage.consts import RATELIMIT_URL


class TokenChecker:
    ''' github api related util class '''

    def __init__(self):
        pass

    # Following are functions to check GitHub's API call rate limits
    def ratelimit(self, username, token):
        r = requests.get(RATELIMIT_URL, auth=(username, token))
        rdict = json.loads(r.text)
        return rdict

    def rate_remaining(self, username, token):
        r = requests.get(RATELIMIT_URL, auth=(username, token))
        rdict = json.loads(r.text)
        try:
            return int(rdict["rate"]["remaining"])
        except:
            return 0

    def rate_reset(self, username, token):
        r = requests.get(RATELIMIT_URL, auth=(username, token))
        rdict = json.loads(r.text)
        wait_time = int(rdict["rate"]["reset"]) - int(time.time())
        return max(0, wait_time)

    def core_remaining(self, username, token):
        r = requests.get(RATELIMIT_URL, auth=(username, token))
        rdict = json.loads(r.text)
        return int(rdict["resources"]["core"]["remaining"])

    def core_reset(self, username, token):
        r = requests.get(RATELIMIT_URL, auth=(username, token))
        rdict = json.loads(r.text)
        wait_time = int(rdict["resources"]["core"]
                        ["reset"]) - int(time.time()) + 3
        return max(0, wait_time)

    def search_remaining(self, username, token):
        r = requests.get(RATELIMIT_URL, auth=(username, token))
        try:
            rdict = json.loads(r.text)
            return int(rdict["resources"]["search"]["remaining"])
        except:
            return 0

    def search_reset(self, username, token):
        r = requests.get(RATELIMIT_URL, auth=(username, token))
        rdict = json.loads(r.text)
        wait_time = int(rdict["resources"]["search"]
                        ["reset"]) - int(time.time()) + 3
        return max(0, wait_time)

    def code_remaining(self, username, token):
        r = requests.get(RATELIMIT_URL, auth=(username, token))
        rdict = json.loads(r.text)
        return int(rdict["resources"]["code_scanning_upload"]["remaining"])

    def code_reset(self, username, token):
        r = requests.get(RATELIMIT_URL, auth=(username, token))
        rdict = json.loads(r.text)
        wait_time = int(
            rdict["resources"]["code_scanning_upload"]["reset"]) - int(time.time()) + 3
        return max(0, wait_time)
