"""Lambda handler for taking routine OpenSearch snapshots

This handler is built in to this construct library and provides a standard method for automatically
taking snapshots of the OpenSearch instance. As such, the dependencies for this runtime code are specified in the
pyproject.toml file for the library as a whole under a separate dependency group for clarity.
"""
# Standard
from datetime import datetime, timedelta, timezone
import logging
import os
import string
from typing import Callable, List, Optional
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
# Unset or empty disables pruning, so every snapshot is kept
snapshot_retention_days = os.environ.get("SNAPSHOT_RETENTION_DAYS", "")

# Only snapshots this handler created are candidates for deletion
SNAPSHOT_NAME_PREFIX = "os_snapshot_"
# Bounds the work done deleting in one invocation. A repository with a large backlog of expired snapshots is
# drained over several daily runs rather than in one.
MAX_DELETIONS_PER_RUN = 20
# Deleting a snapshot is synchronous and can take minutes on a large repository. Pruning stops starting new
# deletions once less than this much of the invocation remains, which leaves time to take the day's snapshot.
RESERVED_SECONDS_FOR_SNAPSHOT = 180

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


def select_snapshots_to_delete(
    snapshots: List[dict],
    now: datetime,
    retention_days: int,
    max_deletions: int = MAX_DELETIONS_PER_RUN,
) -> List[str]:
    """Choose which snapshots have aged out of the retention window

    Only snapshots named with SNAPSHOT_NAME_PREFIX are considered, so snapshots taken by hand or by other tooling
    are never deleted. The most recent SUCCESS snapshot is always kept, even when it is older than the retention
    window, so a run of failed snapshots can never prune the repository down to nothing restorable. Snapshots that
    are still IN_PROGRESS are skipped.

    Parameters
    ----------
    snapshots : list[dict]
        Entries from the get-snapshots API, each with "snapshot", "state" and "start_time_in_millis".
    now : datetime
        Timezone-aware current time.
    retention_days : int
        Snapshots started more than this many days before `now` are eligible for deletion.
    max_deletions : int
        Upper bound on the number of names returned.

    Returns
    -------
    list[str]
        Snapshot names to delete, oldest first.
    """
    ours = [
        s for s in snapshots
        if s.get("snapshot", "").startswith(SNAPSHOT_NAME_PREFIX) and "start_time_in_millis" in s
    ]
    successful = [s for s in ours if s.get("state") == "SUCCESS"]
    newest_success: Optional[str] = (
        max(successful, key=lambda s: s["start_time_in_millis"])["snapshot"] if successful else None
    )
    cutoff_millis = int((now - timedelta(days=retention_days)).timestamp() * 1000)

    expired = [
        s for s in ours
        if s["start_time_in_millis"] < cutoff_millis
        and s.get("state") != "IN_PROGRESS"
        and s["snapshot"] != newest_success
    ]
    expired.sort(key=lambda s: s["start_time_in_millis"])
    return [s["snapshot"] for s in expired[:max_deletions]]


def prune_expired_snapshots(repo_url: str, retention_days: int, remaining_seconds: Callable[[], float]) -> List[str]:
    """Delete snapshots older than the retention window through the OpenSearch snapshot API

    Deleting through the API, rather than expiring objects in S3, is what keeps the repository consistent: OpenSearch
    removes only the files that no remaining snapshot references.

    Parameters
    ----------
    repo_url : str
        Snapshot repository URL, e.g. https://<endpoint>/_snapshot/<repo>
    retention_days : int
        Retention window in days.
    remaining_seconds : Callable[[], float]
        Returns the seconds left in this invocation. No request is started within the last
        RESERVED_SECONDS_FOR_SNAPSHOT seconds, and each request's timeout is capped so this handler stops waiting
        before then. A timed-out delete keeps running on the cluster; only this handler stops waiting for it.

    Returns
    -------
    list[str]
        Names of the snapshots that were deleted.
    """
    budget = remaining_seconds() - RESERVED_SECONDS_FOR_SNAPSHOT
    if budget <= 0:
        logger.info("Skipping pruning to leave time for the snapshot.")
        return []
    response = requests.get(
        f"{repo_url}/_all",
        auth=awsauth,
        params={"filter_path": "snapshots.snapshot,snapshots.state,snapshots.start_time_in_millis"},
        timeout=budget,
    )
    if response.status_code != 200:
        raise Exception(f"Listing snapshots failed: {response.status_code}.{response.text}")
    snapshots = response.json().get("snapshots", [])

    to_delete = select_snapshots_to_delete(snapshots, datetime.now(timezone.utc), retention_days)
    logger.info(
        f"{len(snapshots)} snapshots in repository, {len(to_delete)} selected for deletion "
        f"(retention {retention_days} days, at most {MAX_DELETIONS_PER_RUN} per run)."
    )

    deleted = []
    for name in to_delete:
        budget = remaining_seconds() - RESERVED_SECONDS_FOR_SNAPSHOT
        if budget <= 0:
            logger.info(f"Stopping after {len(deleted)} deletions to leave time for the snapshot.")
            break
        response = requests.delete(f"{repo_url}/{name}", auth=awsauth, timeout=budget)
        if response.status_code != 200:
            raise Exception(f"Deleting snapshot {name} failed: {response.status_code}.{response.text}")
        logger.info(f"Deleted expired snapshot {name}.")
        deleted.append(name)
    return deleted


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

    # Prune before taking the new snapshot so deletions have normally finished by the time it starts (a delete this
    # handler stopped waiting for may still be running). A pruning failure must not cost the day's snapshot, so it
    # is recorded here and raised only after the snapshot has been requested.
    pruning_error: Optional[Exception] = None
    if snapshot_retention_days:
        try:
            prune_expired_snapshots(
                host + f"_snapshot/{snapshot_repo_name}",
                int(snapshot_retention_days),
                lambda: context.get_remaining_time_in_millis() / 1000,
            )
        except Exception as e:
            logger.error(f"Pruning expired snapshots failed: {e}")
            pruning_error = e
    else:
        logger.info("SNAPSHOT_RETENTION_DAYS is not set; keeping all snapshots.")

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

    if pruning_error is not None:
        raise pruning_error
