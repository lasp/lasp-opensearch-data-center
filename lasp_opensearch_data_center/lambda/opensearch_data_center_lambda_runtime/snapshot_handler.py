"""Lambda handler for taking routine OpenSearch snapshots

This handler is built in to this construct library and provides a standard method for automatically
taking snapshots of the OpenSearch instance. As such, the dependencies for this runtime code are specified in the
pyproject.toml file for the library as a whole under a separate dependency group for clarity.
"""
# Standard
from datetime import datetime, timezone
import logging
import os
import string
# Installed
import boto3
import requests
from requests_aws4auth import AWS4Auth


# Lambda env variables
host = os.environ["OPEN_SEARCH_ENDPOINT"]
region = os.environ["AWS_REGION"]
snapshot_repo_name = os.environ["SNAPSHOT_REPO_NAME"]
snapshot_s3_bucket = os.environ["SNAPSHOT_S3_BUCKET"]
snapshot_role_arn = os.environ["SNAPSHOT_ROLE_ARN"]

# AWS service and credentials to pass to the opensearch python library
service = "es"
credentials = boto3.Session().get_credentials()
awsauth = AWS4Auth(
    credentials.access_key,
    credentials.secret_key,
    region,
    service,
    session_token=credentials.token,
)

logger = logging.getLogger(__name__)


def register_repo(payload: dict, url: string):
    """Register the snapshot repo

    Parameters
    ----------
    payload : dict
             S3 bucket and AWS region to store the manual snapshots
             The role ARN that has S3 permissions to store the new snapshot
    url : str
        OpenSearch domain URL endpoint including https:// and trailing /.
    """

    headers = {"Content-Type": "application/json"}

    r = requests.put(url, auth=awsauth, json=payload, headers=headers)
    return r


def take_snapshot(url: string):
    """Initiate a new snapshot

        Parameters
    ----------
    url : str
        OpenSearch domain URL endpoint including https:// and trailing /.
    """

    r = requests.put(url, auth=awsauth)
    return r


def handler(event, context):
    """Top level handler for Lambda invocation for the Snapshot Handler lambda

    The following handler creates a snapshot of an OpenSearch instance, parameterized
    by environment variables.
    """
    # Setup logging
    # Generate new snapshot name with current timestamp
    snapshot_start_time: str = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H:%M:%S")
    snapshot_name = f"os_snapshot_{snapshot_start_time}"
    print("Testing printing")
    logging.basicConfig(level=logging.INFO, force=True)  # Overwrites the pre-existing handler added by Lambda
    logger.info(f"Starting process for snapshot: {snapshot_name}.")

    # Register the snapshot, this can be run every time, if the repo is registered will return 200
    try:
        path = f"_snapshot/{snapshot_repo_name}"  # the OpenSearch API endpoint
        url = host + path

        payload = {
            "type": "s3",
            "settings": {
                "bucket": f"{snapshot_s3_bucket}",
                "region": f"{region}",
                "role_arn": f"{snapshot_role_arn}",
            },
        }
        response = register_repo(payload, url)
        if response.status_code == 200:
            logger.info(f"Repo successfully registered")
        else:
            raise Exception(f"{response.status_code}.{response.text}")
    except Exception as e:
        logger.info(
            f"Snapshot repo registration: {snapshot_repo_name} failed with error code/text: {e}"
        )
        raise

    # Initiate a new manual snapshot
    logger.info("Requesting a new snapshot be taken.")
    try:
        path = f"_snapshot/{snapshot_repo_name}/{snapshot_name}"
        url = host + path
        response = take_snapshot(url)
        if response.status_code == 200:
            logger.info(f"Snapshot {snapshot_name} initiated.")
        else:
            raise Exception(f"{response.status_code}.{response.text}")
    except Exception as e:
        logger.info(
            f"Snapshot initiation for {snapshot_name} failed with error code/text: {e}"
        )
        raise
    logger.info("Response looks good. Snapshot should be in the bucket.")
