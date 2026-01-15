"""
Driver for archiving (sunsetting) indices that exceed a configured size threshold.
The Lambda function can be executed standalone for monitoring or as part of a state machine for automated archival
"""

# Standard
import logging
import os
from datetime import datetime, timezone
import re
import json
import functools
# Installed
import boto3
# Local
from helpers import get_opensearch_client

logger = logging.getLogger(__name__)


class ArchivalError(Exception):
    """
    Custom exception for archival failures with structured context.
    
    Attributes
    ----------
    orig_index : str
        The original OpenSearch index being archived.
    new_index : str
        The new OpenSearch index where archived data will be sent.
    original_error : Exception
        The original exception that caused this failure, if any.
    message : str
        A human-readable error message describing the failure.
    """

    def __init__(self, message: str, orig_index: str = None, new_index: str = None, original_error: Exception = None):
        super().__init__(message)
        self.orig_index = orig_index
        self.new_index = new_index
        self.original_error = original_error
        self.message = message

    def __str__(self):
        parts = [f"Message: {self.message}"]
        if self.orig_index:
            parts.append(f"Original Index: {self.orig_index}")
        if self.new_index:
            parts.append(f"New Index: {self.new_index}")
        if self.original_error:
            parts.append(f"Original Error: {repr(self.original_error)}")
        return " | ".join(parts)

def handler(event, context):
    """ 
    Lambda entry point that routes execution based on the event payload. 
    Executes scheduled large-index checks when run normally, or executes specific steps when triggered by the state machine.
    """

    step = event.get("step") # If the state machine is executing the event will have the "step" in it
    if step == "find_large_indexes":
        return find_large_indexes(event)
    elif step == "kickoff_archival":
        return kickoff_archival(event)
    elif step == "poll_reindex_task":
        return poll_reindex_task(event)
    elif step == "cleanup_archival":
        return cleanup_archival(event)
    else:
        raise ValueError(f"Invalid or missing 'step' in event: {event}")
    
def alert_on_failure(func):
    """
    Decorator that automatically sends an SNS alert when a function raises an exception.

    This decorator wraps a function and catches any exceptions it raises. When an exception occurs, it:
        1. Logs the full stack trace using
        2. Constructs a slack alert message
        3. Sends the alert via `send_sns_message`
        4. Re-raises the original exception

    This is useful for centralizing error handling and alerting 
    """
    @functools.wraps(func) # preserves function name and docstring , useful for logging and testing
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)   # call the original function
        except Exception as e:
            # Check if exception is an ArchivalError
            if isinstance(e, ArchivalError):
                msg = {
                    "msg": f"Failure in {func.__name__}",
                    "error": e.message,
                    "orig_index": e.orig_index,
                    "new_index": e.new_index,
                    "original_error": str(e.original_error),
                    "args": args,
                }
            else:
                msg = {
                    "msg": f"Failure in {func.__name__}",
                    "error": str(e),
                    "args": args,
                }
            logger.exception(f"Error in {func.__name__}")
            send_sns_message("GeneralAlert", "General Alert", msg)
            raise
    return wrapper


@alert_on_failure
def find_large_indexes(event):
    """
    Step 0: Scan OpenSearch for large indices exceeding the threshold.
    Returns a list of index names to archive.
    """

    execution_input = event.get("execution_input", {})
    index_threshold_bytes = (1024 ** 3) * float(execution_input.get("threshold_override", 
                                                os.environ.get("INDEX_SIZE_THRESHOLD_GB", "30")))

    logger.info(f"Using a threshold of {str(index_threshold_bytes)} bytes")
    print("****************************")
    print(event)
    try:
        client = get_opensearch_client()
    except Exception as e:
        logger.error(f"Failed to get OpenSearch Client: {e}")
        raise

    # Query OpenSearch for indices and their sizes
    try:
        # The 'store.size' field returned by OpenSearch's cat.indices API with bytes="b" is expected to be a string
        # representing the size in bytes, sometimes with a trailing 'b' (e.g., "12345b"). We use rstrip('b') to remove
        # the trailing 'b' if present, ensuring we get the numeric value as a string for further processing.
        response = client.cat.indices(format="json", bytes="b")
        indices = [(index["index"], index["store.size"].rstrip('b')) for index in response]
    except Exception as e:
        logger.error(f"Failed to retrieve indices: {e}")
        raise
    
    large_indexes = []

    for index, index_size in indices:
        print("#######")
        print(index)
        print(index_size)
        print(index_threshold_bytes)
        logger.debug(f"On index {index} of size {index_size} bytes")

        # Skip indices that have already been archived
        if re.search(r'-\d{8}$', index):
            logger.info(f"Skipping archived index: {index}")
            continue

        # Skip system indices
        if index.startswith('.'):
            logger.info(f"Skipping system index: {index}")
            continue

        if int(index_size) >= index_threshold_bytes:
            client.indices.refresh(index=index) # Refresh the index to ensure all new data is searchable

            logger.info({
                "msg": f"Index {index} identified for archival",
                "index_size_bytes": index_size,
                "index_size_threshold_bytes": index_threshold_bytes,
            })

            #send_large_index_alert(index, index_size, int(index_threshold_bytes/(1024 ** 3)))
            large_indexes.append(index)
    return large_indexes
    
@alert_on_failure
def kickoff_archival(event):
    """
    Step 1: Kick off the archival for a single index.
    
    Steps performed:
    1. Validates that the source index exists and the target index does not.
    2. Blocks writes to the source index.
    3. Creates a new index with the original mapping and updated settings.
    4. Starts an asynchronous reindex operation.
    """

    index = event.get("index")
    if not index:
        logger.error("No 'index' key found in event payload. Cannot proceed with archival.")
        raise ValueError("Missing 'index' in event payload.")
    
    try:
        client = get_opensearch_client()
    except Exception as e:
        logger.error(f"Failed to get OpenSearch Client: {e}")
        raise ValueError(f"Failed to get OpenSearch Client: {e}")
    
    # Check that the index exists
    if not client.indices.exists(index=index):
        logger.error(f"Index {index} does not exist. Cannot proceed with archival.")
        raise ValueError(f"Index {index} does not exist.")
    
    # Check that new index does not exist
    new_index = f"{index}-{datetime.now(timezone.utc).strftime('%m%d%Y')}"
    if client.indices.exists(index=new_index):
        logger.error(f"Index {new_index} already exists. Cannot proceed with archival")
        raise ValueError(f"Index {new_index} already exists.")

    # Block writes to the original index
    try:
        client.indices.put_settings(
            index=index,
            body={
                "settings": {
                    "index.blocks.read_only": True
                }
            }
        )
        logger.info(f"Successfully set index {index} to read-only mode.")
    except Exception as e:
        logger.error(f"Failed to set index {index} to read-only mode: {e}")
        raise ArchivalError(
            f"Failed to set index {index} to read-only mode. Aborting Archival", 
            orig_index=index,
            original_error=e
        )
    # Retrieve the mapping and settings of the original index
    try:
        original_mapping = client.indices.get_mapping(index=index)
        original_settings = client.indices.get_settings(index=index)

        # Remove/update settings that cannot be applied to the new index
        settings_to_apply = original_settings[index]["settings"]["index"]
        settings_to_apply["number_of_replicas"] = "0" # Keep no replicas during reindexing to improve performance
        settings_to_apply.pop("uuid", None)           # 'uuid' is a unique identifier for the index, automatically generated by OpenSearch.
        settings_to_apply.pop("version", None)        # 'version' tracks the internal version of the index and is managed by OpenSearch.
        settings_to_apply.pop("creation_date", None)  # 'creation_date' is a system-generated setting.
        settings_to_apply.pop("provided_name", None)  # 'provided_name' is also system-generated.
        settings_to_apply.pop("blocks", None)         # Need to remove the write block so we can copy data into the new index
    except Exception as e:
        logger.error(f"Failed to retrieve mapping or settings for index {index}: {e}. Skipping index")
        raise ArchivalError(
            f"Failed to retrieve mapping or settings for index {index}. Aborting Archival", 
            orig_index=index,
            original_error=e
        )


    # Create the new index with the same mapping and settings
    try:
        client.indices.create(
            index=new_index,
            body={
                "settings": settings_to_apply,
                "mappings": original_mapping[index]["mappings"]
            }
        )
        logger.info(f"Successfully created new index {new_index} with original mapping and updated settings.")
    except Exception as e:
        logger.error(f"Failed to create new index {new_index} with original mapping and updated settings: {e}. Skipping index")
        raise ArchivalError(
            f"Failed to create new index {new_index} with original mapping and updated setting. Aborting Archival", 
            orig_index=index,
            new_index=new_index,
            original_error=e
        )

    # Reindex all data to the new index
    try:
        logger.info({
            "msg": "Starting reindexing operation",
            "source": index, 
            "target": new_index
        })
        
        task_id = reindex(index, new_index, client) 
        return {
            "index": index,
            "new_index": new_index,
            "task_id": task_id,
            "status": "IN_PROGRESS", 
            "step": "poll_reindex_task"
        }
    except Exception as e:
        logger.error(f"Failed to reindex {index} into {new_index}. Cleaning up and then skipping archival...")
        
        # Delete new index
        try:
            client.indices.delete(index=new_index)
            logger.info(f"Successfully deleted index {new_index} after failed archival")
        except Exception as e:
            logger.error(f"Failed to delete index {new_index} after failed archival: {e}")
            raise ArchivalError(
                f"Failed to delete index {new_index} after failed archival", 
                orig_index=index,
                new_index=new_index,
                original_error=e
            )
        
        # Remove write block from orig index
        try:
            client.indices.put_settings(
                index=index,
                body={
                    "settings": {
                        "index.blocks.read_only": False
                    }
                }
            )
            logger.info(f"Successfully set index {index} to read-write mode.")
        except Exception as e:
            logger.error(f"Failed to remove write block on index {index}: {e}")
            raise ArchivalError(
                f"Failed to remove write block on index {index}. Aborting Archival", 
                orig_index=index,
                new_index=new_index,
                original_error=e
            )

        raise ArchivalError(
            f"Failed to reindex {index} into {new_index}. Aborting Archival",
            orig_index=index,
            new_index=new_index,
            original_error=e
        )
@alert_on_failure
def poll_reindex_task(event):
    """Step 2: Polls OpenSearch task status."""
    task_id = event.get("task_id")
    try:
        client = get_opensearch_client()
    except Exception as e:
        logger.error(f"Failed to get OpenSearch Client: {e}")
        raise ValueError(f"Failed to get OpenSearch Client: {e}")

    # Poll the task status until it completes
    task_status = client.tasks.get(task_id=task_id)
    if task_status["completed"]:
        logger.info(f"Reindexing task {task_id} completed successfully.")

        return {
            "index": event.get("index"),
            "new_index": event.get("new_index"),
            "task_id": task_id,
            "status": "COMPLETED",
            "step": "cleanup_archival"
        }    
    else:
        logger.info(f"Reindexing task {task_id} is not yet complete.")
        return {
            "index": event.get("index"),
            "new_index": event.get("new_index"),
            "task_id": task_id,
            "status": "IN_PROGRESS",
            "step": "poll_reindex_task"
        }
    
@alert_on_failure
def cleanup_archival(event):
    """
    Step 3: Finalize archival after reindex completes.
    
    Steps performed:
    1. Verifies no data loss between the original and new index.
    2. Unblocks writes to the original index.
    3. Updates replica count on the new index to match the original.
    4. Deletes the original index.
    """

    index = event.get("index")
    new_index = event.get("new_index")

    try:
        client = get_opensearch_client()
    except Exception as e:
        logger.error(f"Failed to get OpenSearch Client: {e}")
        raise ValueError(f"Failed to get OpenSearch Client: {e}")
    
    try:
        client.indices.refresh(index=new_index)
    except Exception as e:
        logger.error(f"Unable to refresh index {new_index}: {e}")
        raise ValueError(f"Unable to refresh index {new_index}: {e}")


    # Check new index for data loss
    orig_doc_count = 0
    new_doc_count = 0
    try:
        orig_doc_count = client.count(index=index)["count"]  # Get document count in orginal index
        new_doc_count = client.count(index=new_index)["count"]   # Get document count in new index
    except Exception as e:
        logger.warning({
            "msg": f"Failed to get the document count for an index: {e}. Its possible some data was lost during reindexing",
            "orig_doc_count": orig_doc_count,
            "new_doc_count": new_doc_count
            })

    if orig_doc_count != new_doc_count:
        logger.warning({
            "msg": "WARNING: some documents were lost during reindexing",
            "original_index": index,
            "original_doc_count": orig_doc_count,
            "new_index": new_index,
            "new_doc_count": new_doc_count
        })
        raise ArchivalError(
            "WARNING: some documents were lost during reindexing. Aborting Archival",
            orig_index=index,
            new_index=new_index,
        )
    
    # Unblock writes to the original index so it can be deleted
    try:
        client.indices.put_settings(
            index=index,
            body={
                "settings": {
                    "index.blocks.read_only": False
                }
            }
        )
        logger.info(f"Successfully set index {index} to read-write mode.")
    except Exception as e:
        logger.error(f"Failed to remove write block on index {index}: {e}")
        raise ArchivalError(
            f"Failed to remove write block on index {index}. Aborting Archival",
            orig_index=index,
            new_index=new_index,
            original_error=e
        )
    
    # Add replicas to the new index
    try:
        original_settings = client.indices.get_settings(index=index)
        num_replicas = original_settings[index]["settings"]["index"]["number_of_replicas"] # getting replica count from original index

        client.indices.put_settings(
            index=new_index,
            body={
                "settings": {
                    "number_of_replicas": num_replicas
                }
            }
        )
        logger.info(f"Successfully added replicas to {new_index}.")
    except Exception as e:
        logger.error(f"Failed to add replicas to {new_index}: {e}")
        raise ArchivalError(
            f"Failed to add replicas to {new_index}. Aborting Archival",
            orig_index=index,
            new_index=new_index,
            original_error=e
        )

    logger.info(f"Successfully archived {index} into {new_index}. Attempting to delete {index}....")

    # Delete the old index
    try:
        client.indices.delete(index=index)
        logger.info(f"Successfully deleted index {index}")
    except Exception as e:
        logger.error(f"Failed to delete index {index} after archival: {e}")
        raise ArchivalError(
            f"Failed to delete index {index} after archival. Aborting Archival",
            orig_index=index,
            new_index=new_index,
            original_error=e
        )
    
    # Create an index alias so archived indexes can be queried as a single index
    try:
        client.indices.update_aliases(
            body={
                "actions": [
                    {"add": {"index": f"{index}*", "alias": f"{index}-combined"}}
                ]
            }
        )
        logger.info(f"Ensured index alias exists: {index}-combined")
    except Exception as e:
        logger.error(f"Failed to create an index alias for {index}: {e}")
        raise ArchivalError(
            f"Failed to create an index alias for {index}: {e}",
            orig_index=index,
            new_index=new_index,
            original_error=e
        )
    
    # Send success message
    try:
        msg = {
            "msg": f"Completed archival of index {index} into {new_index}",
        }
        send_sns_message("GeneralAlert", "General Alert", msg)
    except Exception as e:
        logger.error(f"Failed to send archival success message to Slack: {e}")
        raise

    return {
        "index": index,
        "new_index": new_index,
        "status": "ARCHIVED"
    }

def send_large_index_alert(index:str, index_size_bytes:int, index_size_threshold_gb:int):
    """ 
    Sends an SNS notification and Slack-compatible message for a large index.
    """
    sns_topic_arn = os.environ.get("SNS_TOPIC_ARN")
    if not sns_topic_arn:
        logger.warning("SNS_TOPIC_ARN is not set. Skipping large index alert SNS notification.")
        return
    logger.info("SNS_TOPIC_ARN is set. Proceeding with large index alert SNS notification.")

    
    # Get AWS account ID (for visibility in multi-account setups)
    sts = boto3.client('sts')
    account_id = sts.get_caller_identity()['Account']

    # Construct message payload
    # For custom chatbot message structure see https://docs.aws.amazon.com/chatbot/latest/adminguide/custom-notifs.html
    msg_content = {
        "textType": "client-markdown",
        "title": "Large Index Alert",
        "description": (
            "*A Large Index was Identified for Archival*\n"
            f"- Account ID: `{account_id}`\n"
            f"- Archival start time: `{datetime.now(timezone.utc).replace(tzinfo=None).isoformat()} UTC`\n"
            f"- index_size_threshold_gb: `{index_size_threshold_gb}`\n"
            f"Archival of index {index} of size {int(index_size_bytes) / (1024 ** 3):.2f} GB has been started\n"
            f"\n"
            f"_Note: It takes approximately 5 minutes to reindex 22GB of data on a r7g.large.search instance_"
        )
    }

    message = {
        "version": "1.0",
        "source": "custom",
        "content": msg_content,
        "metadata": {
            "enableCustomActions": False,
        }
    }

    # Send SNS notification
    sns_client = boto3.client("sns")
    try:
        sns_client.publish(
            TopicArn = sns_topic_arn,
            Subject="Large Index Alert",
            Message=json.dumps(message),
            MessageAttributes = { 
            "ErrorType": {
                "DataType": "String",
                "StringValue": "LargeIndexAlert"
            }
        }
        )
        logger.info(f"SNS large index alert sent: {message}")
    except Exception as e:
        logger.exception(f"Failed to send SNS notification: {e}")
        raise

def reindex(index:str, new_index:str, client) -> str: 
    """
    Asynchronously reindexes data from an existing OpenSearch index to a new index.
    Uses multiple slices to parallelize the operation for improved performance.
    """
    try:
        original_settings = client.indices.get_settings(index=index)
        # It's recommended to use a number of slices close to or slightly higher than the number of primary shards in your source index
        # slices split reindexing into parallel sub-tasks
        num_shards = int(original_settings[index]["settings"]["index"]["number_of_shards"])
        num_slices = min(num_shards * 2, 64)  # capped at 64 to prevent excessive parallelization


        body = { 
            "source": {
                "index": index
            },
            "dest": {
                "index": new_index
            }
        }
        res = client.reindex(body=body, wait_for_completion=False, slices=num_slices)  # Start reindexing without waiting 
        task_id = res["task"]  # Extract the task ID

        return task_id
    except Exception as e:
        logger.error(f"Failed to reindex {index} into {new_index}: {e}")
        raise ArchivalError(
            f"Failed to reindex {index} into {new_index}. Aborting Archival",
            orig_index=index,
            new_index=new_index,
            original_error=e
        )

def send_sns_message(error_type: str, subject: str, msg_content: dict) -> None:
    """
    Sends a formatted SNS notification using the AWS Chatbot-compatible message structure.

    This function wraps the low-level SNS `publish()` call, adds standard metadata,
    and logs the outcome.

    Parameters
    ----------
    error_type : str
        A string that categorizes the error (e.g., "IngestError", "GeneralAlert").
        Sent as a message attribute to facilitate filtering or routing.
    
    subject : str
        The subject line of the SNS message.
    
    msg_content : dict
        The message body, formatted according to the AWS Chatbot `client-markdown` structure.
        Must include keys like "textType", "title", and "description".
    """
    message = {
        "textType": "client-markdown",
        "title": subject,
        "description": msg_content
    }
    sns_topic_arn = os.environ.get("SNS_TOPIC_ARN")
    if not sns_topic_arn:
        logger.warning("SNS_TOPIC_ARN is not set. Skipping SNS notification.")
        return
    logger.info("SNS_TOPIC_ARN is set. Proceeding with SNS notification.")

    message = {
        "version": "1.0",
        "source": "custom",
        "content": msg_content,
        "metadata": {
            "enableCustomActions": False,
        }
    }

    # Send SNS notification
    sns_client = boto3.client("sns")
    try:
        sns_client.publish(
            TopicArn = sns_topic_arn,
            Subject=subject,
            Message=json.dumps(message),
            MessageAttributes = {
            "ErrorType": {
                "DataType": "String",
                "StringValue": error_type
            }
        }
        )
        logger.info(f"SNS ingest alert sent: {message}")
    except Exception as e:
        logger.exception(f"Failed to send SNS notification: {e}")