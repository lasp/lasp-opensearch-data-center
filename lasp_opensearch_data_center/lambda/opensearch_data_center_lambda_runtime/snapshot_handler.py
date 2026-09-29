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
from typing import List, Optional
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

# Snapshots are named SNAPSHOT_NAME_PREFIX followed by their start time in SNAPSHOT_TIME_FORMAT. Pruning only
# considers names in exactly this form. The name cannot prove this handler took a snapshot, so a snapshot taken by
# other means is left alone only if it is named differently.
SNAPSHOT_NAME_PREFIX = "os_snapshot_"
SNAPSHOT_TIME_FORMAT = "%Y-%m-%d-%H:%M:%S"
# Bounds the work done deleting in one invocation. A repository with a large backlog of expired snapshots is
# drained over several daily runs rather than in one.
MAX_DELETIONS_PER_RUN = 20
# Only snapshots in a finished state are deleted. The listing leaves the state out when the repository has no record
# of it, and such a snapshot is kept rather than guessed about.
DELETABLE_STATES = ("SUCCESS", "FAILED", "PARTIAL")
# Timeout for the snapshot listing. `requests` applies it to connecting and to each wait for data, not to the whole
# response.
LISTING_TIMEOUT_SECONDS = 120
# How long to wait for the deletion request to answer. OpenSearch queues a deletion while a snapshot is writing to
# the same repository, and the day's snapshot has just been requested, so the deletion normally cannot finish
# within one invocation. The request is left running on the cluster once this wait is over.
DELETE_WAIT_SECONDS = 60

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


def snapshot_name(start_time: datetime) -> str:
    """Name of the snapshot this handler takes at `start_time`"""
    return SNAPSHOT_NAME_PREFIX + start_time.strftime(SNAPSHOT_TIME_FORMAT)


def snapshot_start_time(name: str) -> datetime:
    """Start time encoded in a name `snapshot_name` produced, as a UTC datetime"""
    return datetime.strptime(name[len(SNAPSHOT_NAME_PREFIX):], SNAPSHOT_TIME_FORMAT).replace(tzinfo=timezone.utc)


def is_handler_snapshot_name(name: str) -> bool:
    """Whether `name` is exactly in the form `snapshot_name` produces"""
    if not name.startswith(SNAPSHOT_NAME_PREFIX):
        return False
    timestamp = name[len(SNAPSHOT_NAME_PREFIX):]
    try:
        # strptime accepts unpadded fields, so compare the round trip rather than trusting the parse alone
        return datetime.strptime(timestamp, SNAPSHOT_TIME_FORMAT).strftime(SNAPSHOT_TIME_FORMAT) == timestamp
    except ValueError:
        return False


def select_snapshots_to_delete(
    snapshots: List[dict],
    now: datetime,
    retention_days: int,
    max_deletions: int = MAX_DELETIONS_PER_RUN,
) -> List[str]:
    """Choose which snapshots have aged out of the retention window

    Only snapshots whose names are exactly in the form `snapshot_name` produces are considered, and each one's age is
    read from its name. A snapshot taken by hand or by other tooling is never deleted unless it is given a name in
    that form. The most recent SUCCESS snapshot among those considered, ignoring names dated after `now`, is always
    kept even when it is older than the retention window, so a run of failed snapshots never prunes away the last one
    the repository reports as successful. That is a record of the snapshot, not proof it can be restored: files
    removed from the bucket by other means are not detected. Only snapshots in DELETABLE_STATES are deleted, so
    IN_PROGRESS snapshots, which a deletion would abort, and snapshots with no recorded state are kept.

    Parameters
    ----------
    snapshots : list[dict]
        Entries from the get-snapshots API, each with "snapshot" and "state".
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
        (snapshot_start_time(s["snapshot"]), s) for s in snapshots if is_handler_snapshot_name(s.get("snapshot", ""))
    ]
    # A name dated in the future (a clock error, or one given by hand) must not take the place of the real newest
    successful = [(started, s) for started, s in ours if s.get("state") == "SUCCESS" and started <= now]
    newest_success: Optional[str] = max(successful, key=lambda pair: pair[0])[1]["snapshot"] if successful else None
    cutoff = now - timedelta(days=retention_days)

    expired = [
        (started, s) for started, s in ours
        if started < cutoff
        and s.get("state") in DELETABLE_STATES
        and s["snapshot"] != newest_success
    ]
    expired.sort(key=lambda pair: pair[0])
    return [s["snapshot"] for _, s in expired[:max_deletions]]


def prune_expired_snapshots(repo_url: str, retention_days: int) -> List[str]:
    """Delete snapshots older than the retention window through the OpenSearch snapshot API

    Deleting through the API, rather than expiring objects in S3, is what keeps the repository consistent: OpenSearch
    removes only the files that no remaining snapshot references.

    The listing uses verbose=false, which OpenSearch answers from the repository index alone. A verbose listing also
    reads each snapshot's own metadata file and fails outright if any one is missing, as it is for snapshots whose
    files an S3 expiry rule removed; those are exactly the snapshots that most need deleting.

    All selected snapshots are deleted in a single request. If it has not answered within DELETE_WAIT_SECONDS, or the
    endpoint answers 504, it is left running on the cluster. If a selected snapshot no longer exists (another
    deletion finished first), OpenSearch rejects the whole request. In each case the next run's listing selects
    again whatever was not deleted.

    Parameters
    ----------
    repo_url : str
        Snapshot repository URL, e.g. https://<endpoint>/_snapshot/<repo>
    retention_days : int
        Retention window in days.

    Returns
    -------
    list[str]
        Names of the snapshots whose deletion was requested.
    """
    response = requests.get(
        f"{repo_url}/_all",
        auth=awsauth,
        params={"verbose": "false", "filter_path": "snapshots.snapshot,snapshots.state"},
        timeout=LISTING_TIMEOUT_SECONDS,
    )
    if response.status_code != 200:
        raise Exception(f"Listing snapshots failed: {response.status_code}.{response.text}")
    snapshots = response.json().get("snapshots", [])

    to_delete = select_snapshots_to_delete(snapshots, datetime.now(timezone.utc), retention_days)
    logger.info(
        f"{len(snapshots)} snapshots in repository, {len(to_delete)} selected for deletion "
        f"(retention {retention_days} days, at most {MAX_DELETIONS_PER_RUN} per run)."
    )
    if not to_delete:
        return []

    try:
        response = requests.delete(f"{repo_url}/{','.join(to_delete)}", auth=awsauth, timeout=DELETE_WAIT_SECONDS)
    except requests.exceptions.ReadTimeout:
        # The request was sent (failing to connect raises ConnectTimeout or ConnectionError instead, which fail the
        # invocation). OpenSearch answers a deletion only once it completes, and abandoning the wait does not cancel it.
        logger.warning(
            f"Deletion of {to_delete} did not finish within {DELETE_WAIT_SECONDS} seconds and continues on the cluster."
        )
        return to_delete
    if response.status_code == 504:
        # AWS documents this for taking snapshots, and it is applied here to deleting them: "Long-running snapshot
        # operations sometimes encounter the following error: 504 GATEWAY_TIMEOUT. You can typically ignore these
        # errors and wait for the operation to complete successfully." (Amazon OpenSearch Service, "Take a snapshot")
        logger.warning(f"Deletion of {to_delete} timed out at the endpoint (504); it typically still completes.")
        return to_delete
    if response.status_code == 404:
        logger.warning(
            f"A snapshot in {to_delete} no longer exists, so none were deleted; the next run selects them again. "
            f"{response.text}"
        )
        return []
    if response.status_code != 200:
        raise Exception(f"Deleting snapshots {to_delete} failed: {response.status_code}.{response.text}")
    logger.info(f"Deleted expired snapshots {to_delete}.")
    return to_delete


def handler(event, context):
    """Top level handler for Lambda invocation for the Snapshot Handler lambda

    The following handler creates a snapshot of an OpenSearch instance, parameterized
    by environment variables, and then prunes expired snapshots when SNAPSHOT_RETENTION_DAYS is set.
    """
    # Setup logging
    # Generate new snapshot name with current timestamp
    new_snapshot_name = snapshot_name(datetime.now(timezone.utc))
    print("Testing printing")
    logging.basicConfig(level=logging.INFO, force=True)  # Overwrites the pre-existing handler added by Lambda
    logger.info(f"Starting process for snapshot: {new_snapshot_name}.")

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
        path = f"_snapshot/{snapshot_repo_name}/{new_snapshot_name}"
        url = host + path
        response = take_snapshot(url)
        if response.status_code == 200:
            logger.info(f"Snapshot {new_snapshot_name} initiated.")
        else:
            raise Exception(f"{response.status_code}.{response.text}")
    except Exception as e:
        logger.info(
            f"Snapshot initiation for {new_snapshot_name} failed with error code/text: {e}"
        )
        raise
    logger.info("Response looks good. Snapshot should be in the bucket.")

    # Prune only once the day's snapshot has been requested, so that no failure or delay in pruning can prevent it.
    # While the new snapshot is still writing, OpenSearch queues the deletion behind it. The new snapshot is inside
    # the retention window, so pruning cannot select it.
    if snapshot_retention_days:
        retention_days = int(snapshot_retention_days)
        if retention_days < 1:
            raise ValueError(f"SNAPSHOT_RETENTION_DAYS must be at least 1, got {snapshot_retention_days!r}")
        prune_expired_snapshots(host + f"_snapshot/{snapshot_repo_name}", retention_days)
    else:
        logger.info("SNAPSHOT_RETENTION_DAYS is not set; keeping all snapshots.")
