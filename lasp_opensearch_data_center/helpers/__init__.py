import json
import os
import logging
import traceback
from functools import lru_cache
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from multiprocessing.connection import Connection as mp_connection
from typing import NamedTuple
import time
from datetime import date, datetime
import copy
import ssl
# Installed
import boto3
import opensearchpy
from opensearchpy import (
    RequestsHttpConnection,
    AWSV4SignerAuth,
    ConnectionTimeout,
    TransportError, 
    NotFoundError
)
from typing import Any, Optional, Tuple, Mapping
import json
import os
import logging
from collections import defaultdict
import re
# Installed
import boto3

logger = logging.getLogger(__name__)

@lru_cache
def get_opensearch_client() -> opensearchpy.OpenSearch:
    """Creates and returns an OpenSearch client with AWS SigV4 auth, custom retry logic, 
    and environment-based configuration."""

    # Define a custom connection class to handle retryable HTTP requests using requests' retry strategy
    class RetryableConnection(RequestsHttpConnection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            # Define retry behavior for transient HTTP errors and throttling
            #TODO need to decide if we want to keep this retry strategy or go back to the defult
                # if we keep it we should adjust the allowed_methods and status_forcelist to better suit our needs
                # regardless anytime a bulk upload request fails we should not retry
            retry_strategy = Retry(
                total=3, # Total number of retries
                backoff_factor=1, # Exponential backoff factor
                status_forcelist=[429, 500, 502, 503, 504],  # Retry on these HTTP status codes
                allowed_methods=["POST", "GET", "PUT"], # Apply retry to these HTTP methods
            )

            # Attach the retry strategy to HTTPS connections
            adapter = HTTPAdapter(max_retries=retry_strategy)
            self.session.mount("https://", adapter)


    credentials = boto3.Session().get_credentials()
    auth = AWSV4SignerAuth(credentials, "us-west-2")  # Create AWS SigV4 authentication object for signing requests to OpenSearch
     
    # Read configuration from environment variables
    host = os.environ["OPEN_SEARCH_ENDPOINT"]
    port = int(os.environ.get("OPEN_SEARCH_PORT", 443))
    timeout = int(os.environ.get("OPENSEARCH_CLIENT_REQUEST_TIMEOUT", 60))
    use_ssl = os.environ.get("OPEN_SEARCH_USE_SSL", "true").lower() == "true"

    # Instantiate the OpenSearch client with retryable HTTP connection
    client = opensearchpy.OpenSearch(
        hosts=[{"host": host, "port": port}],            # Host configuration
        http_auth=auth,                                  # AWS SigV4 auth
        http_compress=True,                              # Enables gzip compression for request bodies
        use_ssl=use_ssl,                                 # Use HTTPS
        verify_certs=True,                               # Enforce SSL certificate verification
        connection_class=RetryableConnection,            # Inject custom retry logic via connection class
        timeout=timeout,                                 # Timeout in seconds for all requests
        pool_maxsize=10,                                 # Max number of connections to OpenSearch
        ssl_context=ssl.create_default_context()         # Use the default SSL context
    )
    return client

def _json_serialize_default(o: Any) -> str:
    """
    A standard 'default' json serializer function.

    - Serializes datetime objects using their .isoformat() method.

    - Serializes all other objects using repr().
    """
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    return repr(o)


class JsonLogFormatter(logging.Formatter):
    """Altered version of the CloudWatchLogFormatter provided in the watchtower library"""

    _default_log_record_attrs = ('created', 'name', 'module', 'lineno', 'funcName', 'levelname')

    def __init__(
            self,
            *args,
            add_log_record_attrs: Optional[Tuple[str, ...]] = None,
            custom_log_record_attrs: Optional[Mapping[str, Any]] = None,
            add_asctime: bool = True,
            **kwargs,
    ):
        """

        Parameters
        ----------
        add_log_record_attrs : Optional[Tuple[str, ...]]
            Tuple of log record attributes to add to the resulting structured JSON structure that comes out of the
            logging formatter. Default None.
        custom_log_record_attrs : Optional[Mapping[str, Any]]
            Additional static log record attributes to add to the log record. Default None.
        add_asctime : bool
            If True, adds an ASCII (ISO 8601-like) timestamp to the log record. Default True.
        """
        super().__init__(*args, **kwargs)
        self.add_log_record_attrs = add_log_record_attrs or self._default_log_record_attrs
        self.custom_log_record_attrs = custom_log_record_attrs
        self.add_asctime = add_asctime

    def format(self, record: logging.LogRecord) -> str:
        """Format log message to a string

        Parameters
        ----------
        record : logging.LogRecord
            Log record object containing the logged message, which may be a dict (Mapping) or a string
        """
        # Perform %-style string interpolation before we make the message into a dict
        # This allows logging in the `log.info("%s incomplete %s", 1, "message")` style
        if isinstance(record.msg, str) and record.args:
            record.msg = record.msg % record.args
            record.args = None

        # If a dict was passed in, we don't want to mutate it as a side effect so we deepcopy it
        # This is a huge performance hit, but otherwise we are mutating our users' data and that's not cool
        msg = copy.deepcopy(record.msg) if isinstance(record.msg, Mapping) else {"msg": record.msg}

        if self.add_asctime:
            msg["asctime"] = self.formatTime(record)

        # Add additional attributes from the logging system to the msg dict
        if self.add_log_record_attrs:
            for field in self.add_log_record_attrs:
                if field != "msg":
                    msg[field] = getattr(record, field)

        if self.custom_log_record_attrs:
            for field, value in self.custom_log_record_attrs.items():
                msg[field] = value

        # If we logged an exception, add the formatted traceback to the msg dict
        if record.exc_info:
            formatted_traceback = ''.join(traceback.format_exception(*record.exc_info))
            print(formatted_traceback)
            msg["traceback"] = formatted_traceback

        # Modify the record itself with the new msg dict
        record.msg = msg
        return json.dumps(record.msg, default=_json_serialize_default)  # Serialize the msg dict

def configure_itdc_logging(context_attributes: Optional[Mapping[str, Any]] = None) -> None:
    """Configure logging based on a hard-coded logging configuration.

    This is an idempotent configuration function. Calling it more than once simply re-configures the logging system.

    Parameters
    ----------
    context_attributes : Optional[Mapping[str, Any]]
        Specific context attributes to set on every log message. Default None.
    """
    try:
        console_log_level = os.environ['CONSOLE_LOG_LEVEL']
    except KeyError as ke:
        console_log_level = 'DEBUG'

    stream_json_formatter = JsonLogFormatter(
        # Add custom attributes from the logging system into the json structure
        add_log_record_attrs=('levelname', 'process', 'filename', 'name', 'funcName', 'lineno'),
        custom_log_record_attrs=context_attributes,
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(stream_json_formatter)
    console_handler.setLevel(console_log_level)

    # Set up root logger with handlers defined above
    root_logger = logging.getLogger()
    root_logger.setLevel('DEBUG')
    root_logger.propagate = True
    root_logger.handlers = [console_handler]

    # Set nuisance loggers to INFO level
    for logger_name in ("botocore",
                        "boto3",
                        "urllib3",
                        "s3transfer",
                        "opensearch"):
        log = logging.getLogger(logger_name)
        log.setLevel('WARNING')

def send_sns_general_alert(alert: dict, filename: str, ingest_started: str = "N/A") -> None:
    """
    Sends an SNS alert notification if any errors occurred. This is intended for all non ingest related failures

    Parameters
    ----------
    alert : dict
        A dictionary containing the error information that will be sent to slack.
    filename : str
        The name of the file being processed when the error occurred.
    ingest_started : str
        A timestamp indicating when the ingest process started. Expected to be in ISO format
    """

    # Get AWS account ID (for visibility in multi-account setups)
    sts = boto3.client('sts')
    account_id = sts.get_caller_identity()['Account']

    # Construct message payload
    # For custom chatbot message structure see https://docs.aws.amazon.com/chatbot/latest/adminguide/custom-notifs.html
    message = {
        "textType": "client-markdown",
        "title": "General Alert",
        "description": (
            "*A general alert was triggered during processing*\n"
            f"- Account ID: `{account_id}`\n"
            f"- Filename: `{filename}`\n"
            f"- Ingest started on `{ingest_started}`\n"
            f"- Ingest completed on `N/A`\n"
            "*Alerts (may not always indicate a failure):*\n"
            + json.dumps(alert, indent=2)
        )
    }

    send_sns_message("GeneralAlert", "General Alert", message)

def send_sns_message(error_type: str, subject: str, msg_content: dict) -> None:
    """
    Sends a formatted SNS notification using the AWS Chatbot-compatible message structure.

    This function wraps the low-level SNS `publish()` call, adds standard metadata,
    and logs the outcome. It is intended to be used by higher-level alerting functions
    like `send_sns_ingest_alert` and `send_sns_general_alert`.

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

    sns_topic_arn = os.environ.get("SNS_TOPIC_ARN")
    if not sns_topic_arn:
        logger.warning("SNS_TOPIC_ARN is not set. Skipping ingest alert SNS notification.")
        return
    logger.info("SNS_TOPIC_ARN is set. Proceeding with ingest alert SNS notification.")

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