"""S3 client, AWS- and MinIO-compatible (moved from ``s3/client.py``).

Near-verbatim from the pre-re-architecture ``S3Client`` / ``S3Bucket``, with two
non-behavioural tidy-ups: the boto3 log-level quieting runs on first client
construction instead of as an import side effect, and the internal error logs go
through the module logger rather than the root logger.
"""

import logging
import os
from typing import Any

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

_boto_logging_quieted = False


def _quiet_boto_logging() -> None:
    """Silence boto3's chatty INFO logs once, on first client construction."""
    global _boto_logging_quieted
    if _boto_logging_quieted:
        return
    for name in ("boto3", "botocore", "s3transfer"):
        logging.getLogger(name).setLevel(logging.WARNING)
    _boto_logging_quieted = True


class S3Client:
    def __init__(
        self,
        host: str,
        access_key: str,
        port: int,
        secret_access_key: str,
        region_name: str = "us-east-1",
        https: bool = True,
    ) -> None:
        _quiet_boto_logging()
        self.access_key = access_key
        self.secret_access_key = secret_access_key
        scheme = "https" if https else "http"
        self.url = f"{scheme}://{host}:{port}"
        self._s3: Any = boto3.client(
            "s3",
            endpoint_url=self.url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_access_key,
            region_name=region_name,
            use_ssl=https,
        )

    def ensure_bucket(self, bucket_name: str) -> None:
        try:
            self._s3.head_bucket(Bucket=bucket_name)
            logger.info("Bucket '%s' already exists.", bucket_name)
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if str(error_code) == "404":
                logger.info("Creating bucket '%s'...", bucket_name)
                try:
                    self._s3.create_bucket(
                        Bucket=bucket_name,
                        CreateBucketConfiguration={"LocationConstraint": self._s3.meta.region_name},
                    )
                except ClientError as create_err:
                    if create_err.response["Error"]["Code"] == "BucketAlreadyOwnedByYou":
                        logger.info("Bucket '%s' was created by another process.", bucket_name)
                    else:
                        raise
            else:
                raise


class S3Bucket:
    def __init__(self, client: S3Client, bucket_name: str) -> None:
        self.client = client
        self.bucket_name = bucket_name
        self.client.ensure_bucket(bucket_name)
        logger.debug("Configuring bucket %s", self)

    def __str__(self) -> str:
        return f"{self.client.url}/{self.bucket_name}"

    def upload_file(self, file_name: str, object_name: str | None = None) -> bool:
        """Upload a file to an S3 bucket; return True on success."""
        if object_name is None:
            object_name = os.path.basename(file_name)
        try:
            self.client._s3.upload_file(file_name, self.bucket_name, object_name)
        except ClientError as e:
            logger.error("upload_file failed: %s", e)
            return False
        return True

    def download_file(self, object_name: str, file_path: str) -> bool:
        """Download a file from an S3 bucket; return True on success."""
        try:
            self.client._s3.download_file(self.bucket_name, object_name, file_path)
        except ClientError as e:
            logger.error("download_file failed: %s", e)
            return False
        return True

    def object_exists(self, object_name: str) -> bool:
        """Check if an object exists in the bucket."""
        try:
            self.client._s3.head_object(Bucket=self.bucket_name, Key=object_name)
            return True
        except ClientError:
            return False

    def put_bytes(self, object_name: str, data: bytes) -> bool:
        """Write raw bytes as an S3 object; return True on success."""
        try:
            self.client._s3.put_object(Bucket=self.bucket_name, Key=object_name, Body=data)
            return True
        except ClientError as e:
            logger.error("put_bytes failed: %s", e)
            return False
