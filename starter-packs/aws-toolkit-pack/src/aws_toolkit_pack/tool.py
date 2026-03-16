"""AWS toolkit for S3, EC2, and Lambda operations using boto3."""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError


def _get_session(region: str, **kwargs) -> boto3.Session:
    """Build a boto3 session from explicit credentials or environment."""
    session_kwargs: dict[str, Any] = {"region_name": region}
    if kwargs.get("aws_access_key_id"):
        session_kwargs["aws_access_key_id"] = kwargs["aws_access_key_id"]
    if kwargs.get("aws_secret_access_key"):
        session_kwargs["aws_secret_access_key"] = kwargs["aws_secret_access_key"]
    if kwargs.get("aws_session_token"):
        session_kwargs["aws_session_token"] = kwargs["aws_session_token"]
    if kwargs.get("profile_name"):
        session_kwargs["profile_name"] = kwargs["profile_name"]
    return boto3.Session(**session_kwargs)


# ── S3 ───────────────────────────────────────────────────────────────────

def _s3(session: boto3.Session, operation: str, **kwargs) -> dict:
    s3 = session.client("s3")

    if operation == "list_buckets":
        resp = s3.list_buckets()
        buckets = [
            {"name": b["Name"], "created": b["CreationDate"].isoformat()}
            for b in resp.get("Buckets", [])
        ]
        return {"status": "ok", "buckets": buckets, "count": len(buckets)}

    elif operation == "list_objects":
        bucket = kwargs["bucket"]
        prefix = kwargs.get("prefix", "")
        max_keys = kwargs.get("max_keys", 1000)
        resp = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, MaxKeys=max_keys)
        objects = []
        for obj in resp.get("Contents", []):
            objects.append({
                "key": obj["Key"],
                "size": obj["Size"],
                "last_modified": obj["LastModified"].isoformat(),
            })
        return {
            "status": "ok",
            "bucket": bucket,
            "prefix": prefix,
            "objects": objects,
            "count": len(objects),
            "truncated": resp.get("IsTruncated", False),
        }

    elif operation == "upload":
        bucket = kwargs["bucket"]
        key = kwargs["key"]
        file_path = kwargs["file_path"]
        s3.upload_file(file_path, bucket, key)
        return {
            "status": "ok",
            "message": f"Uploaded {file_path} to s3://{bucket}/{key}",
            "bucket": bucket,
            "key": key,
        }

    elif operation == "download":
        bucket = kwargs["bucket"]
        key = kwargs["key"]
        file_path = kwargs.get("file_path", os.path.basename(key) or "download")
        s3.download_file(bucket, key, file_path)
        return {
            "status": "ok",
            "message": f"Downloaded s3://{bucket}/{key} to {file_path}",
            "file_path": file_path,
        }

    else:
        return {"status": "error", "message": f"Unknown S3 operation: {operation}. Valid: list_buckets, list_objects, upload, download"}


# ── EC2 ──────────────────────────────────────────────────────────────────

def _ec2(session: boto3.Session, operation: str, **kwargs) -> dict:
    ec2 = session.client("ec2")

    if operation == "list_instances":
        filters = kwargs.get("filters", [])
        resp = ec2.describe_instances(Filters=filters)
        instances = []
        for reservation in resp.get("Reservations", []):
            for inst in reservation.get("Instances", []):
                name = ""
                for tag in inst.get("Tags", []):
                    if tag["Key"] == "Name":
                        name = tag["Value"]
                        break
                instances.append({
                    "instance_id": inst["InstanceId"],
                    "name": name,
                    "state": inst["State"]["Name"],
                    "type": inst["InstanceType"],
                    "public_ip": inst.get("PublicIpAddress"),
                    "private_ip": inst.get("PrivateIpAddress"),
                    "launch_time": inst["LaunchTime"].isoformat() if inst.get("LaunchTime") else None,
                })
        return {"status": "ok", "instances": instances, "count": len(instances)}

    elif operation == "start":
        instance_id = kwargs["instance_id"]
        ec2.start_instances(InstanceIds=[instance_id])
        return {"status": "ok", "message": f"Starting instance {instance_id}", "instance_id": instance_id}

    elif operation == "stop":
        instance_id = kwargs["instance_id"]
        ec2.stop_instances(InstanceIds=[instance_id])
        return {"status": "ok", "message": f"Stopping instance {instance_id}", "instance_id": instance_id}

    else:
        return {"status": "error", "message": f"Unknown EC2 operation: {operation}. Valid: list_instances, start, stop"}


# ── Lambda ───────────────────────────────────────────────────────────────

def _lambda(session: boto3.Session, operation: str, **kwargs) -> dict:
    lam = session.client("lambda")

    if operation == "list_functions":
        resp = lam.list_functions()
        functions = []
        for fn in resp.get("Functions", []):
            functions.append({
                "name": fn["FunctionName"],
                "runtime": fn.get("Runtime"),
                "handler": fn.get("Handler"),
                "memory": fn.get("MemorySize"),
                "timeout": fn.get("Timeout"),
                "last_modified": fn.get("LastModified"),
            })
        return {"status": "ok", "functions": functions, "count": len(functions)}

    elif operation == "invoke":
        function_name = kwargs["function_name"]
        payload = kwargs.get("payload", {})
        invocation_type = kwargs.get("invocation_type", "RequestResponse")
        resp = lam.invoke(
            FunctionName=function_name,
            InvocationType=invocation_type,
            Payload=json.dumps(payload).encode(),
        )
        response_payload = resp["Payload"].read().decode("utf-8")
        try:
            response_payload = json.loads(response_payload)
        except json.JSONDecodeError:
            pass
        return {
            "status": "ok",
            "function_name": function_name,
            "status_code": resp["StatusCode"],
            "response": response_payload,
            "executed_version": resp.get("ExecutedVersion"),
        }

    else:
        return {"status": "error", "message": f"Unknown Lambda operation: {operation}. Valid: list_functions, invoke"}


# ── Main entry point ────────────────────────────────────────────────────

_SERVICE_MAP = {
    "s3": _s3,
    "ec2": _ec2,
    "lambda": _lambda,
}


def run(service: str, operation: str, region: str = "us-east-1", **kwargs) -> dict:
    """Execute an AWS operation.

    Parameters
    ----------
    service : str
        AWS service – one of ``s3``, ``ec2``, ``lambda``.
    operation : str
        Service-specific operation name.
    region : str
        AWS region (default ``us-east-1``).
    **kwargs :
        Service/operation-specific parameters plus optional credentials
        (aws_access_key_id, aws_secret_access_key, aws_session_token, profile_name).

    Returns
    -------
    dict with ``status`` and operation-specific data.
    """
    handler = _SERVICE_MAP.get(service)
    if handler is None:
        return {
            "status": "error",
            "message": f"Unknown service: {service}. Valid services: {', '.join(_SERVICE_MAP)}",
        }

    try:
        session = _get_session(region, **kwargs)
        return handler(session, operation, **kwargs)
    except KeyError as exc:
        return {"status": "error", "message": f"Missing required parameter: {exc}"}
    except ClientError as exc:
        error = exc.response.get("Error", {})
        return {
            "status": "error",
            "message": f"AWS error ({error.get('Code', 'Unknown')}): {error.get('Message', str(exc))}",
        }
    except BotoCoreError as exc:
        return {"status": "error", "message": f"AWS SDK error: {exc}"}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
