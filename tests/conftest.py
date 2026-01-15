"""Pytest configuration for unit testing"""
# Standard
import logging
# Installed
import pytest
from testcontainers.opensearch import OpenSearchContainer
from lasp_opensearch_data_center.lambda_functions.helpers import get_opensearch_client

@pytest.fixture
def cleanup_loggers():
    """Ensures that root logging handlers are removed after a test"""
    yield
    root = logging.getLogger()
    root.handlers = []

@pytest.fixture(scope='session')
def monkeypatch_session():
    """Provides a monkeypatch that applies for an entire pytest session (saves time)"""
    from _pytest.monkeypatch import MonkeyPatch
    m = MonkeyPatch()
    yield m
    m.undo()


# Set fake credentials for Opensearch
@pytest.fixture(scope='session', autouse=True)
def mock_aws_credentials(monkeypatch_session):
    """Mocked AWS Credentials."""
    monkeypatch_session.setenv('AWS_ACCESS_KEY_ID', 'testing')
    monkeypatch_session.setenv('AWS_SECRET_ACCESS_KEY', 'testing')
    monkeypatch_session.setenv('AWS_SECURITY_TOKEN', 'testing')
    monkeypatch_session.setenv('AWS_SESSION_TOKEN', 'testing')
    monkeypatch_session.setenv('AWS_REGION', 'us-west-2')
    monkeypatch_session.delenv('AWS_PROFILE', raising=False)
    monkeypatch_session.delenv('AWS_DEFAULT_REGION', raising=False)

@pytest.fixture(scope="session")
def opensearch_container():
    """
    Spin up a real OpenSearch 2.x in Docker for the duration of the test session.
    security_enabled=False → no passwords / TLS.
    """
    with OpenSearchContainer(security_enabled=False) as osc:
        yield osc         

@pytest.fixture()
def _opensearch_env(opensearch_container, monkeypatch):
    host = opensearch_container.get_container_host_ip()
    port = opensearch_container.get_exposed_port(9200)
    monkeypatch.setenv("OPEN_SEARCH_ENDPOINT", host)
    monkeypatch.setenv("OPEN_SEARCH_PORT", str(port))
    monkeypatch.setenv("OPENSEARCH_CLIENT_REQUEST_TIMEOUT", "60")
    monkeypatch.setenv("OPEN_SEARCH_USE_SSL", "False")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    # If you cache the client, clear that cache before test starts
    get_opensearch_client.cache_clear()
    yield
    get_opensearch_client.cache_clear()

@pytest.fixture(autouse=True)
def _wipe_opensearch(opensearch_container):
    # Clear opensearch before testing
    client = opensearch_container.get_client()
    
    for index in client.indices.get("*").keys():
        client.indices.delete(index=index, ignore=[404])
    for t in client.indices.get_index_template().get("index_templates", []):
        client.indices.delete_index_template(name=t["name"], ignore=[404])

    yield

    # Clear opensearch after testing
    client = opensearch_container.get_client()
    for index in client.indices.get("*").keys():
        client.indices.delete(index=index, ignore=[404])
    for t in client.indices.get_index_template().get("index_templates", []):
        client.indices.delete_index_template(name=t["name"], ignore=[404])
