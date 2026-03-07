'''
S3 client. AWS and Minio compatible

Alex Duly Nov 25

'''
import os
import boto3
import logging
from botocore.exceptions import ClientError
# from concurrent.futures import ThreadPoolExecutor
# from boto3.s3.transfer import TransferConfig


# needed for some weird config 
# config = TransferConfig(use_threads=False)
# Quiet boto3 logs
logging.getLogger("boto3").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)
logging.getLogger("s3transfer").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)


class S3Client:
    def __init__(self, host, access_key: str, port: int,secret_access_key: str,  region_name: str = "us-east-1", https: bool = True):
        self.access_key = access_key
        self.secret_access_key = secret_access_key
        scheme = "https" if https else "http"
        self.url = f"{scheme}://{host}:{port}"
        self._s3 = boto3.client(
            "s3",
            endpoint_url=self.url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_access_key,
            region_name=region_name,
            use_ssl=https
        )

    def ensure_bucket(self, bucket_name: str):
        try:
            self._s3.head_bucket(Bucket=bucket_name)
            logger.info(f"Bucket '{bucket_name}' already exists.")
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if str(error_code) == '404':
                logger.info(f"Creating bucket '{bucket_name}'...")
                try:
                    self._s3.create_bucket(
                        Bucket=bucket_name,
                        CreateBucketConfiguration={'LocationConstraint': self._s3.meta.region_name}
                    )
                except ClientError as create_err:
                    if create_err.response['Error']['Code'] == 'BucketAlreadyOwnedByYou':
                        logger.info(f"Bucket '{bucket_name}' was created by another process.")
                    else:
                        raise
            else:
                raise e


class S3Bucket:
    def __init__(self, client: S3Client, bucket_name: str):
        self.client = client
        self.bucket_name = bucket_name
        self.client.ensure_bucket(bucket_name)
        logger.debug(f"Configuring bucket {self}")

    def __str__(self):
        return f"{self.client.url}/{self.bucket_name}"

    def upload_file(self, file_name, object_name=None):
        """Upload a file to an S3 bucket

        :param file_name: File to upload
        :param object_name: S3 object name. If not specified then file_name is used
        :return: True if file was uploaded, else False
        """

        # If S3 object_name was not specified, use file_name
        if object_name is None:
            object_name = os.path.basename(file_name)

        # Upload the file
        try:
            self.client._s3.upload_file(file_name, self.bucket_name, object_name) #, config=Config)
        except ClientError as e:
            logging.error(e)
            return False
        return True
    
    def download_file(self, object_name: str, file_path: str)->bool:
        """Download a file from S3 bucket

        :param file_path: Path to download to 
        :param object_name: S3 object name.
        :return: True if file was uploaded, else False
        """
        try:
            self.client._s3.download_File(file_path, self.bucket_name, object_name) 
        except ClientError as e:
            logging.error(e)
            return False
        return True


