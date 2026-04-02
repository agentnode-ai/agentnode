import asyncio

import boto3
from botocore.config import Config

from app.config import settings

_client = None
_public_client = None


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


def _get_public_s3_client():
    """Client using the public endpoint for generating presigned download URLs."""
    global _public_client
    if _public_client is None:
        endpoint = settings.S3_PUBLIC_ENDPOINT or settings.S3_ENDPOINT
        _public_client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=settings.S3_ACCESS_KEY,
            aws_secret_access_key=settings.S3_SECRET_KEY,
            region_name=settings.S3_REGION,
            config=Config(signature_version="s3v4"),
        )
    return _public_client


async def upload_artifact(object_key: str, data: bytes, content_type: str = "application/gzip") -> None:
    client = get_s3_client()
    await asyncio.to_thread(
        client.put_object,
        Bucket=settings.S3_BUCKET,
        Key=object_key,
        Body=data,
        ContentType=content_type,
    )


async def download_artifact(object_key: str) -> bytes:
    """Download an artifact from S3 and return the raw bytes."""
    client = get_s3_client()
    response = await asyncio.to_thread(
        client.get_object, Bucket=settings.S3_BUCKET, Key=object_key
    )
    return await asyncio.to_thread(response["Body"].read)


async def delete_artifact(object_key: str) -> None:
    """Delete an object from S3."""
    client = get_s3_client()
    await asyncio.to_thread(
        client.delete_object, Bucket=settings.S3_BUCKET, Key=object_key
    )


async def generate_presigned_url(object_key: str, expires_in: int = 900) -> str:
    """Generate a presigned download URL. Default expiry: 15 minutes."""
    client = _get_public_s3_client()
    return await asyncio.to_thread(
        client.generate_presigned_url,
        "get_object",
        Params={"Bucket": settings.S3_BUCKET, "Key": object_key},
        ExpiresIn=expires_in,
    )


# --- Preview file storage for file browser ---

PREVIEW_EXTENSIONS = {".md", ".py", ".ts", ".js", ".json", ".yaml", ".yml", ".toml", ".txt", ".cfg", ".ini"}
PREVIEW_MAX_BYTES = 500 * 1024  # 500KB
PREVIEW_MAX_LINES = 2000

_CONTENT_TYPE_MAP = {
    ".md": "text/markdown", ".py": "text/x-python", ".ts": "text/typescript",
    ".js": "text/javascript", ".json": "application/json", ".yaml": "text/yaml",
    ".yml": "text/yaml", ".toml": "text/toml", ".txt": "text/plain",
    ".cfg": "text/plain", ".ini": "text/plain",
}


def _preview_key(version_id: str, file_path: str) -> str:
    return f"previews/{version_id}/{file_path}"


async def upload_preview_file(version_id: str, file_path: str, content: str) -> str:
    """Upload a preview file to S3. Returns the object key."""
    import os
    ext = os.path.splitext(file_path)[1].lower()
    content_type = _CONTENT_TYPE_MAP.get(ext, "text/plain")
    key = _preview_key(version_id, file_path)
    client = get_s3_client()
    await asyncio.to_thread(
        client.put_object,
        Bucket=settings.S3_BUCKET,
        Key=key,
        Body=content.encode("utf-8"),
        ContentType=content_type,
        CacheControl="public, max-age=31536000, immutable",
    )
    return key


async def download_preview_file(version_id: str, file_path: str) -> str | None:
    """Download a preview file from S3. Returns content string or None."""
    key = _preview_key(version_id, file_path)
    try:
        client = get_s3_client()
        response = await asyncio.to_thread(
            client.get_object, Bucket=settings.S3_BUCKET, Key=key
        )
        body = await asyncio.to_thread(response["Body"].read)
        return body.decode("utf-8")
    except client.exceptions.NoSuchKey:
        return None
    except Exception:
        return None
