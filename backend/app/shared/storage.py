import boto3
from botocore.config import Config

from app.config import settings

_client = None


def get_s3_client():
    global _client
    if _client is None:
        _client = boto3.client(
            "s3",
            endpoint_url=settings.S3_ENDPOINT,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION,
            config=Config(signature_version="s3v4"),
        )
    return _client


def upload_artifact(object_key: str, data: bytes, content_type: str = "application/gzip") -> None:
    client = get_s3_client()
    client.put_object(
        Bucket=settings.S3_BUCKET,
        Key=object_key,
        Body=data,
        ContentType=content_type,
    )


def generate_presigned_url(object_key: str, expires_in: int = 900) -> str:
    """Generate a presigned download URL. Default expiry: 15 minutes."""
    client = get_s3_client()
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": object_key},
        ExpiresIn=expires_in,
    )
