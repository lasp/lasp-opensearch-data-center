"""Pytest configuration for unit testing"""
# Standard
import logging
# Installed
import pytest

pytest_plugins = [
    "tests.plugins.common_fixtures",
    "tests.plugins.data_path_fixtures",
    "tests.plugins.aws_moto_fixtures",
    "tests.plugins.opensearch_fixtures"
]


@pytest.fixture
def cleanup_loggers():
    """Ensures that root logging handlers are removed after a test"""
    yield
    root = logging.getLogger()
    root.handlers = []
