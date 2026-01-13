"""Test ingesting a CSV file of packet data"""
# Installed
import pytest
from moto import mock_aws
from cloudpathlib import S3Path
# Local
from itdc_lambda_runtime.handlers.ingest_handler import handler
from itdc_lambda_runtime.helpers import get_opensearch_client
from ....common_plugins.check_ingest_utilities import _test_file_ingest_assertions


@pytest.fixture(autouse=True)
def _set_ingester_env_variables(monkeypatch, ingest_bucket):
    monkeypatch.setenv("BUCKET_NAME", ingest_bucket.name)
    monkeypatch.setenv("CHUNK_SIZE_MB", "5")
    monkeypatch.setenv("CONSOLE_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("GENERATE_IDS", "1")
    monkeypatch.setenv("INGEST_STATUS_FILE_NAME_GSI", "file_name_index")
    monkeypatch.setenv("INGEST_STATUS_TABLE", "ingest_status")
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "100")
    monkeypatch.setenv("MAX_PROCESSES", "25")
    monkeypatch.setenv("OPENSEARCH_CLIENT_REQUEST_TIMEOUT", "60")
    monkeypatch.setenv("OPEN_SEARCH_USE_SSL", "False")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")

@mock_aws
@pytest.mark.parametrize(
    ("test_file_name", "n_records_in_opensearch_expected", "n_records_parsed_expected", "file_type", "n_parsing_errors_expected", "index_name"),
    [
        (
            "ssim1_libera-em_0_test-packet-name_2023001t112233.csv",
            28120,
            28120,
            "packet",
            0,
            "libera-em-test-packet-name-packets"
        ),
        (
            "ssim1_libera-em_0_hydra-status_2024271t100652.csv",
            1623, # Every row is duplicated
            3246,
            "packet",
            0,
            "libera-em-hydra-status-packets"
        ),
        (
            "ssim1_libera-em_0_axis-full-el_2024299t152645_4.csv",
            4094, # Various rows appear many times
            5845,
            "packet",
            0,
            "libera-em-axis-full-el-packets"
        ),
        (
            "ssim1_libera-fm_0_icie-log-msg_2025120t235747.csv",
            20,
            20,
            "packet",
            0,
            "ssim1_libera-fm-icie-log-msg-packets"
        )
    ]
)
def test_packet_ingest(
        ingest_bucket,
        ingest_status_table,
        test_data_path,
        write_file_to_s3,
        test_file_name,
        monkeypatch,
        n_records_in_opensearch_expected,
        n_records_parsed_expected,
        file_type,
        n_parsing_errors_expected,
        index_name,
        _opensearch_env
):

    """Test ingesting a CSV file of packet data"""
    print("Testing ingest of a CSV file of packet data")
    test_file = test_data_path / test_file_name
    s3_path = S3Path(f"s3://{ingest_bucket.name}/{test_file.name}")
    event = write_file_to_s3(test_file, s3_path)

    handler(event, None)

    _test_file_ingest_assertions(
        ingest_bucket=ingest_bucket,
        ingest_status_table=ingest_status_table,
        test_file=test_file,
        opensearch_client=get_opensearch_client(),
        n_records_in_opensearch_expected=n_records_in_opensearch_expected,
        n_records_parsed_expected=n_records_parsed_expected,
        file_type=file_type,
        n_parsing_errors_expected=n_parsing_errors_expected,
        index_name=index_name
    )


@mock_aws
@pytest.mark.parametrize(
    ("test_file_name", "n_records_in_opensearch_expected", "file_type", "n_parsing_errors_expected", "index_name"),
    [
        (
            "ssim1_libera-em_0_event-log_2024077t093848.log",
            142,
            "event-logs",
            0,
            "libera-em-event-log"
        ),
        (
            "ssim1_libera-em_0_EventLog_2024284t111711.txt",
            646,
            "event-logs",
            0,
            "libera-em-event-log"
        ),
    ]
)
def test_event_log_ingest(
        ingest_bucket,
        ingest_status_table,
        test_data_path,
        write_file_to_s3,
        test_file_name,
        n_records_in_opensearch_expected,
        file_type,
        n_parsing_errors_expected,
        index_name,
        _opensearch_env
):
    """Test ingesting an event log file"""
    print("Testing ingest of an event log file")
    test_file = test_data_path / test_file_name

    # Upload the file to the dropbox bucket
    s3_path = S3Path(f"s3://{ingest_bucket.name}/{test_file.name}")
    event = write_file_to_s3(test_file, s3_path)

    handler(event, None)
    
    _test_file_ingest_assertions(
        ingest_bucket=ingest_bucket,
        ingest_status_table=ingest_status_table,
        test_file=test_file,
        opensearch_client=get_opensearch_client(),
        n_records_in_opensearch_expected=n_records_in_opensearch_expected,
        file_type=file_type,
        n_parsing_errors_expected=n_parsing_errors_expected,
        index_name=index_name
    )


@mock_aws
@pytest.mark.parametrize(
    ("test_file_name", "n_records_in_opensearch_expected", "file_type", "n_parsing_errors_expected", "index_name"),
    [
        (
            "ssim1_libera-em_1142_test-summary_2025189t165212.txt",
            1,
            "test-summaries",
            0,
            "libera-em-test-summaries"
        ),
        (
            "ssim1_libera-em_1143_test-summary_2025189t190804.txt",
            1,
            "test-summaries",
            0,
            "libera-em-test-summaries"
        ),
        (
            "ssim1_libera-fm_1144_test-summary_2025190t164504.txt",
            1,
            "test-summaries",
            0,
            "libera-fm-test-summaries"
        ),
        (
            "ssim1_libera-fm_1145_test-summary_2025190t204958.txt",
            1,
            "test-summaries",
            0,
            "libera-fm-test-summaries"
        ),
    ]
)
def test_test_summary_ingest(
        ingest_bucket,
        ingest_status_table,
        test_data_path,
        write_file_to_s3,
        test_file_name,
        n_records_in_opensearch_expected,
        file_type,
        n_parsing_errors_expected,
        index_name,
        _opensearch_env
):
    """Test ingesting a test summary file"""
    print("Testing ingest of a test summary file")
    test_file = test_data_path / test_file_name

    # Upload the file to the dropbox bucket
    s3_path = S3Path(f"s3://{ingest_bucket.name}/{test_file.name}")
    event = write_file_to_s3(test_file, s3_path)

    handler(event, None)

    _test_file_ingest_assertions(
        ingest_bucket=ingest_bucket,
        ingest_status_table=ingest_status_table,
        test_file=test_file,
        opensearch_client=get_opensearch_client(),
        n_records_in_opensearch_expected=n_records_in_opensearch_expected,
        file_type=file_type,
        n_parsing_errors_expected=n_parsing_errors_expected,
        index_name=index_name
    )
