import os
import logging
from functools import lru_cache
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
import ssl
# Installed
import boto3
import opensearchpy
from opensearchpy import (
    RequestsHttpConnection,
    AWSV4SignerAuth
)

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
            #TODO need to decide if we want to keep this retry strategy or go back to the default
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

    region = os.environ.get("AWS_REGION", "us-west-2")
    credentials = boto3.Session().get_credentials()
    auth = AWSV4SignerAuth(credentials, region)  # Create AWS SigV4 authentication object for signing requests to OpenSearch
     
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