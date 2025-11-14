import boto3
import logging
from botocore.exceptions import ClientError

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
        self.buckets: dict[str, str] = {}  # name -> location (optional)

    def ensure_bucket(self, bucket_name: str):
        try:
            self._s3.head_bucket(Bucket=bucket_name)
            logger.info(f"Bucket '{bucket_name}' already exists.")
        except ClientError as e:
            error_code = int(e.response['Error']['Code'])
            if error_code == 404:
                logger.info(f"Creating bucket '{bucket_name}'...")
                self._s3.create_bucket(
                    Bucket=bucket_name,
                    CreateBucketConfiguration={'LocationConstraint': self._s3.meta.region_name}
                )
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

    def upload_file(self, file_path: str, key: str):
        self.client._s3.upload_file(file_path, self.bucket_name, key)

    def download_file(self, key: str, file_path: str):
        self.client._s3.download_file(self.bucket_name, key, file_path)


class ArtifactBucket(S3Bucket):
    """Bucket used to store successfully built binaries and associated artifacts"""
    def __init__(self, client: S3Client):
        super().__init__(client, "artifacts")



class ProjectBucket(S3Bucket):
    """Bucket used to store scraped GitHub projects"""
    def __init__(self, client: S3Client):
        super().__init__(client, "project-archive")
        

