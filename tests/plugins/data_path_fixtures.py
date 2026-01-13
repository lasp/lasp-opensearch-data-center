"""Common fixtures for both unit and integration testing for accessing test data files"""
# Standard
from pathlib import Path
import sys
# Installed
import pytest


@pytest.fixture
def test_data_path():
    """Returns the Path to the test_data directory"""
    return Path(sys.modules[__name__.split('.')[0]].__file__).parent / 'test_data'


@pytest.fixture
def test_packet_file(test_data_path):
    """Path to the test packet data file"""
    return test_data_path / 'ssim1_libera-em_0_test-packet-name_2023001t112233.csv'


@pytest.fixture
def hydra_status_packet_file(test_data_path):
    """Path to a hydra-status test packet data file"""
    return test_data_path / 'ssim1_libera-em_0_hydra-status_2024271t100652.csv'


@pytest.fixture
def test_integrated_log_file(test_data_path):
    """Path to the test integrated log file"""
    return test_data_path / 'ssim1_libera-em_0_integrated-log_2023365t112233.log'


@pytest.fixture
def test_event_log_file(test_data_path):
    """Path to test event log file"""
    return test_data_path / "ssim1_libera-em_0_event-log_2024077t093848.log"

