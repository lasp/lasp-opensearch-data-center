"""Constant values use throughout the package."""
# Standard
from enum import Enum

INGEST_STATUS_TABLE_NAME = 'ingest_status'
INGEST_STATUS_TABLE_PK = "file_type"
INGEST_STATUS_TABLE_SK = "ingest_started"

INGEST_STATUS_FILE_NAME_GSI_NAME = "file_name_index"
INGEST_STATUS_FILE_NAME_GSI_PK = "file_name"

OPENSEARCH_SNAPSHOT_REPO_NAME = "opensearch-snapshot-repo"


class DropboxLambdaEnv(Enum):
    """Valid environment variable names for the Dropbox Lambda"""
    DROPBOX_BUCKET_NAME = "DROPBOX_BUCKET_NAME"
    INGEST_BUCKET_NAME = "INGEST_BUCKET_NAME"
    CONSOLE_LOG_LEVEL = "CONSOLE_LOG_LEVEL"


class IngestLambdaEnv(Enum):
    """Valid environment variable names for the Ingest Lambda"""
    OPEN_SEARCH_ENDPOINT = "OPEN_SEARCH_ENDPOINT"
    BUCKET_NAME = "BUCKET_NAME"
    INGEST_STATUS_TABLE = "INGEST_STATUS_TABLE"
    INGEST_STATUS_FILE_NAME_GSI = "INGEST_STATUS_FILE_NAME_GSI"
    CONSOLE_LOG_LEVEL = "CONSOLE_LOG_LEVEL"
    CHUNK_SIZE_MB = "CHUNK_SIZE_MB"
    GENERATE_IDS = "GENERATE_IDS"
    MAX_PROCESSES = "MAX_PROCESSES"
    MAX_FILE_SIZE_MB = "MAX_FILE_SIZE_MB"
    OPENSEARCH_CLIENT_REQUEST_TIMEOUT = "OPENSEARCH_CLIENT_REQUEST_TIMEOUT"
