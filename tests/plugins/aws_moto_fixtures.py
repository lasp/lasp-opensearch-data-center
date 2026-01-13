"""Unit testing fixtures for AWS services

This module should never create real AWS objects but should instead mock them
"""
# Standard
from pathlib import Path
import random
import string
from typing import Union, Optional
# Installed
import boto3
# Installed
from aws_lambda_powertools.utilities.data_classes import SQSEvent
from aws_lambda_powertools.utilities.parser.models import S3Model
from cloudpathlib import S3Client, S3Path
from moto import mock_aws
import pytest
# Local


@pytest.fixture(scope='session', autouse=True)
def mock_aws_credentials(monkeypatch_session):
    """Mocked AWS Credentials for moto."""
    monkeypatch_session.setenv('AWS_ACCESS_KEY_ID', 'testing')
    monkeypatch_session.setenv('AWS_SECRET_ACCESS_KEY', 'testing')
    monkeypatch_session.setenv('AWS_SECURITY_TOKEN', 'testing')
    monkeypatch_session.setenv('AWS_SESSION_TOKEN', 'testing')
    monkeypatch_session.delenv('AWS_PROFILE', raising=False)
    monkeypatch_session.delenv('AWS_REGION', raising=False)
    monkeypatch_session.delenv('AWS_DEFAULT_REGION', raising=False)


@pytest.fixture(scope='session', autouse=True)
def set_up_cloudpathlib_s3client(mock_aws_credentials, monkeypatch_session):
    """This sets the default client used for S3Path objects as a mocked S3 context.
    Make sure this code runs before any code that tries to instantiate an S3Path object without an explicit client.

    This fixture is session scoped so that we don't have to call it every time we use cloudpathlib
    """
    # Tell cloudpathlib to clear its local file cache whenever a file operation is completed.
    # https://cloudpathlib.drivendata.org/stable/caching/#file-cache-mode-close_file
    monkeypatch_session.setenv("CLOUPATHLIB_FILE_CACHE_MODE", "close_file")
    with mock_aws():
        client = S3Client()
        client.set_as_default_client()


@pytest.fixture
def mock_s3_context():
    """Everything under/inherited by this runs in the mock_s3 context manager

    This fixture is function scoped so that S3 buckets get cleared between tests.
    """
    with mock_aws():
        # Yield the (mocked) s3 Resource object
        # (see boto3 docs: https://boto3.amazonaws.com/v1/documentation/api/latest/guide/resources.html)
        yield boto3.resource('s3')


@pytest.fixture
def create_mock_bucket(mock_s3_context):
    """Returns a function that allows dynamic creation of s3 buckets with option to specify the name.

    Note: if the bucket already exists, this doesn't overwrite it. Previous contents will remain.
    Caution: If you create multiple objects at the same location, you may get conflicts"""
    s3 = mock_s3_context

    # The following call to Random() creates a locally seeded random generator. This prevents the pytest-randomly
    # seeded global PRN generator from creating the same "random" bucket names for every test.
    local_random = random.Random()

    def _create_bucket(bucket_name: Optional[str] = None) -> s3.Bucket:
        """Creates a mock bucket, optionally with a custom name.

        Returns
        -------
        : s3.Bucket
        """
        if not bucket_name:
            bucket_name = ''.join(local_random.choice(string.ascii_letters) for _ in range(16))
        bucket = s3.Bucket(bucket_name)
        if not bucket.creation_date:  # If bucket doesn't already exist
            bucket.create()
            print(f"Created mock S3 bucket {bucket}.")
        else:
            print(f"Using existing mock S3 bucket {bucket}. You may see FileExistsErrors if you are writing the same"
                  f" file as a previous test due to the behavior of cloudpathlib S3Path objects.")
        return bucket

    yield _create_bucket


@pytest.fixture
def write_file_to_s3(mock_s3_context, create_mock_bucket):
    """Write file contents to mocked s3 bucket. If the bucket doesn't exist, it is created."""

    def _write(filepath: Path, uri: Union[str, S3Path], exists_ok: bool = False) -> S3Path:
        """Write the contents of the file at filepath to the (mocked) S3 URI.

        Parameters
        ----------
        filepath : Path
            Path object pointing to the file to be put into the S3 bucket.
        uri : str
            Fully specified desired s3 object path (<bucket>/<key>)
        exists_ok : bool, Optional
            Whether it's ok to overwrite an existing object. Default is False.

        Returns
        -------
        : S3Path
            S3Path object
        """
        content = filepath.read_bytes()
        s3_path = S3Path(uri)
        create_mock_bucket(s3_path.bucket)  # Ensure bucket exists
        if not exists_ok and s3_path.exists():
            raise ValueError(f"Object {uri} already exists in mock bucket.")
        s3_path.mkdir(parents=True)  # Make additional directories (key paths) if necessary
        s3_path.write_bytes(content)
        print(f"Wrote {filepath} contents to (mocked) S3 object {s3_path.as_uri()}")

        s3_event_raw = {
                    "Records": [
                        {
                            "eventVersion": "2.2",
                            "eventSource": "aws:s3",
                            "awsRegion": "us-west-2",
                            "eventTime": "2024-10-25T15:55:13.395000Z",
                            "eventName": "ObjectCreated:CompleteMultipartUpload",
                            "userIdentity": {
                                "principalId": "AWS:AROAUT7RU6VQWVH24AYZJ:Gavin.Medley@lasp.colorado.edu"
                            },
                            "requestParameters": {
                                "sourceIPAddress": "71.218.125.143/32"
                            },
                            "responseElements": {
                                "x-amz-request-id": "0R0A402Y16VBS55A",
                                "x-amz-id-2": "Mhalhjzm5ztAO/z9yM+WXoAY2z1eTUoYf58Gw5alxOCA3nCLyBTPGHstZwp1zezTTQhfwxysa/caEbTUF9K/knAoUqOzFDtG"},
                            "s3": {
                                "s3SchemaVersion": "1.0",
                                "configurationId": "OGIwNzEzN2QtYTI2Yi00NTE2LTliN2YtYTYxOTBjNjU2ZGMz",
                                "bucket": {
                                    "name": s3_path.bucket,
                                    "ownerIdentity": {"principalId": "A2LPSOR58T5X3B"},
                                    "arn": "arn:aws:s3:::"+s3_path.bucket
                                },
                                "object": {
                                    "key": s3_path.name,
                                    "size": 20668659,
                                    "eTag": "fad87aca35447962c15ce352b3276b68-10",
                                    "sequencer": "00671BBF5C554D41AF",
                                    "versionId": "uPjXILaZ52EOtDqdaSA4IgEwRxD6rN7t"
                                }
                            },
                            "glacierEventData": None
                        }
                    ]
                }
        # Check that this can be parsed into an S3Model
        s3_model = S3Model(**s3_event_raw)

        sqs_s3_event_raw = {
            "Records": [
                {
                    "messageId": "50177e95-e0ce-438f-9237-a8e72e9cd63d",
                    "receiptHandle": "AQEByUeXzyATzqQQQE2rDORlHJVxq3Qb6iwp9HtSOtMHWzXCfbLbnrljbwQCtutxK8mTx4TacQroctYWkNn2RTbSGVCqtQuK168ZvdSuUM6XiT5x4E6x+uSQufdDQc9O5GLTA7U8GTLME6kRRlBA01onK+Hzbk5hxDwnO0wJyTfVbJzZlt2sxAubDl6YkY7JurYKInFrhj86w5sQ0rNV5bN/rfu40crS8JN8Sk31+ELTiVO8mJPBptYhTlOLxw+ETz9Rdr/Gcii0+Mm3fMODCP0txYOLiQZLqb325XeDSpNYK7dRmMA7+5kZgkSa0oaMF+Z78kXM6uIyqes1kpWK8v/iHEVPm+pzPgnPn+XHmWaX4p0+3JBYgl0JqaLBMUdgun8WsRSyg+Exb2OGGQi99H5oUg==",
                    "body": s3_model.model_dump_json(by_alias=True),  # Dump the raw json string in as the body of the SQSEvent
                    "attributes": {
                        "ApproximateReceiveCount": "1",
                        "SentTimestamp": "1729871713941",
                        "SenderId": "AROAUWHQT37XQVCB4XU5S:S3-PROD-END",
                        "ApproximateFirstReceiveTimestamp": "1729871713950"
                    },
                    "messageAttributes": {},
                    "md5OfBody": "0b62b2150deae8c70ea8d1db966cb34e",
                    "eventSource": "aws:sqs",
                    "eventSourceARN": "arn:aws:sqs:us-west-2:317797496161:DropboxQueue",
                    "awsRegion": "us-west-2"
                }
            ]
        }
        sqs_event = SQSEvent(sqs_s3_event_raw)
            

        return sqs_event

    return _write


@pytest.fixture
def ingest_status_table(mock_aws_credentials):
    """Everything under/inherited by this runs in the mock_dynamodb context manager"""
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-west-2")

        table = dynamodb.create_table(
            TableName="ingest_status",
            KeySchema=[
                {"AttributeName": "file_type", "KeyType": "HASH"},
                {"AttributeName": "ingest_started", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "file_type", "AttributeType": "S"},
                {"AttributeName": "ingest_started", "AttributeType": "S"},
                {"AttributeName": "file_name", "AttributeType": "S"},
            ],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "file_name_index",
                    "KeySchema": [
                        {"AttributeName": "file_name", "KeyType": "HASH"},
                    ],
                    "Projection": {"ProjectionType": "KEYS_ONLY"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                }
            ],
        )
    yield table


@pytest.fixture
def ingest_bucket(mock_aws_credentials):
    with mock_aws():
        s3 = boto3.resource('s3', region_name="us-west-2")
        b = s3.create_bucket(Bucket='ingest-bucket',
                         CreateBucketConfiguration={'LocationConstraint': "us-west-2"})
        yield b

@pytest.fixture
def dropbox_bucket(mock_aws_credentials):
    with mock_aws():
        s3 = boto3.resource('s3', region_name="us-west-2")
        b = s3.create_bucket(Bucket='dropbox-bucket',
                         CreateBucketConfiguration={'LocationConstraint': "us-west-2"})
        yield b