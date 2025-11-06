import boto3

class S3:
    def __init__(self,host, access_key: str, secret_access_key: str, port:int = 9000, region_name: str = "us-east-1", https: bool = True):
        self.access_key = access_key
        self.secret_access_key = secret_access_key
        
        scheme = "https" if https else "http"
        url = f"{scheme}://{host}:{port}"
        self.s3 = boto3.client(
                "s3",
                endpoint_url=url,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_access_key,
                region_name=region_name,
            )