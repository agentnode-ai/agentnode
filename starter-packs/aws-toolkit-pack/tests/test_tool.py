"""Tests for aws-toolkit-pack."""

from unittest.mock import MagicMock, patch
from datetime import datetime

from aws_toolkit_pack.tool import run


# -- Input validation --

def test_unknown_service():
    result = run(service="rds", operation="list")
    assert result["status"] == "error"
    assert "Unknown service" in result["message"]


def test_s3_unknown_operation():
    result = run(service="s3", operation="destroy")
    assert result["status"] == "error"
    assert "Unknown S3 operation" in result["message"]


def test_ec2_unknown_operation():
    result = run(service="ec2", operation="terminate")
    assert result["status"] == "error"
    assert "Unknown EC2 operation" in result["message"]


def test_lambda_unknown_operation():
    result = run(service="lambda", operation="deploy")
    assert result["status"] == "error"
    assert "Unknown Lambda operation" in result["message"]


# -- Mocked S3 list_buckets --

@patch("aws_toolkit_pack.tool.boto3.Session")
def test_s3_list_buckets(mock_session_cls):
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session
    mock_s3 = MagicMock()
    mock_session.client.return_value = mock_s3
    mock_s3.list_buckets.return_value = {
        "Buckets": [{"Name": "my-bucket", "CreationDate": datetime(2024, 1, 1)}],
    }

    result = run(service="s3", operation="list_buckets")
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["buckets"][0]["name"] == "my-bucket"


# -- Mocked EC2 list_instances --

@patch("aws_toolkit_pack.tool.boto3.Session")
def test_ec2_list_instances(mock_session_cls):
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session
    mock_ec2 = MagicMock()
    mock_session.client.return_value = mock_ec2
    mock_ec2.describe_instances.return_value = {
        "Reservations": [{
            "Instances": [{
                "InstanceId": "i-123",
                "State": {"Name": "running"},
                "InstanceType": "t3.micro",
                "PublicIpAddress": "1.2.3.4",
                "PrivateIpAddress": "10.0.0.1",
                "LaunchTime": datetime(2024, 1, 1),
                "Tags": [{"Key": "Name", "Value": "web-server"}],
            }],
        }],
    }

    result = run(service="ec2", operation="list_instances")
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["instances"][0]["name"] == "web-server"


# -- Mocked Lambda list_functions --

@patch("aws_toolkit_pack.tool.boto3.Session")
def test_lambda_list_functions(mock_session_cls):
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session
    mock_lam = MagicMock()
    mock_session.client.return_value = mock_lam
    mock_lam.list_functions.return_value = {
        "Functions": [{
            "FunctionName": "my-func", "Runtime": "python3.12",
            "Handler": "handler.main", "MemorySize": 256,
            "Timeout": 30, "LastModified": "2024-01-01",
        }],
    }

    result = run(service="lambda", operation="list_functions")
    assert result["status"] == "ok"
    assert result["count"] == 1
    assert result["functions"][0]["name"] == "my-func"


# -- Missing parameter error --

@patch("aws_toolkit_pack.tool.boto3.Session")
def test_s3_list_objects_missing_bucket(mock_session_cls):
    mock_session = MagicMock()
    mock_session_cls.return_value = mock_session
    mock_s3 = MagicMock()
    mock_session.client.return_value = mock_s3

    result = run(service="s3", operation="list_objects")
    assert result["status"] == "error"
    assert "Missing required parameter" in result["message"]
