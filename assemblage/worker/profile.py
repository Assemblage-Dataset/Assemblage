"""
Some worker setting classes

"""

from dataclasses import dataclass


@dataclass
class AWSProfile:
    s3_bucket_name: str
    profile_name: str
