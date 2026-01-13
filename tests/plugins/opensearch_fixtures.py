"""Pytest configuration for unit testing"""
# Installed
import pytest

from testcontainers.opensearch import OpenSearchContainer
from lasp_opensearch_data_center.lambda_functions.opensearch_data_center_lambda_runtime.helpers import get_opensearch_client

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
    # If you cache the client, clear that cache before test starts
    get_opensearch_client.cache_clear()
    yield
    get_opensearch_client.cache_clear()

@pytest.fixture(autouse=True)
def _wipe_opensearch(opensearch_container):
    # Clear opensearch before testing
    client = opensearch_container.get_client()
    
    #for index in client.indices.get("*").keys():
    #    client.indices.delete(index=index, ignore=[404])
    #for t in client.indices.get_index_template().get("index_templates", []):
    #    client.indices.delete_index_template(name=t["name"], ignore=[404])

    yield

    # Clear opensearch after testing
    #client = opensearch_container.get_client()
    #for index in client.indices.get("*").keys():
    #    client.indices.delete(index=index, ignore=[404])
    #for t in client.indices.get_index_template().get("index_templates", []):
    #    client.indices.delete_index_template(name=t["name"], ignore=[404])
