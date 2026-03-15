"""Create the S3/MinIO bucket for local development."""
import boto3
from botocore.exceptions import ClientError

from app.config import settings


def create_bucket():
    client = boto3.client(
        "s3",
        endpoint_url=settings.S3_ENDPOINT,
        aws_access_key_id=settings.S3_ACCESS_KEY,
        aws_secret_access_key=settings.S3_SECRET_KEY,
        region_name=settings.S3_REGION,
    )

    try:
        client.create_bucket(Bucket=settings.S3_BUCKET)
        print(f"Bucket '{settings.S3_BUCKET}' created.")
    except ClientError as e:
        if e.response["Error"]["Code"] in ("BucketAlreadyExists", "BucketAlreadyOwnedByYou"):
            print(f"Bucket '{settings.S3_BUCKET}' already exists.")
        else:
            raise


if __name__ == "__main__":
    create_bucket()
