"""Tests for how the constructs configure snapshot storage and retention"""
# Installed
import aws_cdk as cdk
from aws_cdk import aws_certificatemanager as acm, aws_route53 as route53
from aws_cdk.assertions import Match, Template
import pytest
# Local
from lasp_opensearch_data_center.constructs.backend_storage import BackendStorageConstruct
from lasp_opensearch_data_center.constructs.opensearch import OpenSearchConstruct

ENV = cdk.Environment(account="123456789012", region="us-west-2")


def _stack(**opensearch_kwargs) -> cdk.Stack:
    app = cdk.App()
    stack = cdk.Stack(app, "TestStack", env=ENV)
    storage = BackendStorageConstruct(
        stack,
        "Storage",
        dropbox_bucket_name="test-dropbox",
        ingest_bucket_name="test-ingest",
        opensearch_snapshot_bucket_name="test-snapshot",
    )
    zone = route53.HostedZone(stack, "Zone", zone_name="example.com")
    certificate = acm.Certificate(stack, "Cert", domain_name="example.com")
    OpenSearchConstruct(
        stack,
        "OpenSearch",
        environment=ENV,
        hosted_zone=zone,
        certificate=certificate,
        opensearch_snapshot_bucket=storage.opensearch_snapshot_bucket,
        opensearch_domain_name="opensearch",
        **opensearch_kwargs,
    )
    return stack


def _snapshot_lambda_env(template: Template) -> dict:
    functions = template.find_resources(
        "AWS::Lambda::Function",
        {"Properties": {"Environment": {"Variables": {"SNAPSHOT_REPO_NAME": Match.any_value()}}}},
    )
    assert len(functions) == 1, list(functions)
    return next(iter(functions.values()))["Properties"]["Environment"]["Variables"]


def test_snapshot_bucket_has_no_lifecycle_rules():
    """Snapshots are incremental, so expiring repository objects by age corrupts current snapshots."""
    template = Template.from_stack(_stack())
    buckets = template.find_resources("AWS::S3::Bucket", {"Properties": {"BucketName": "test-snapshot"}})
    assert len(buckets) == 1, list(buckets)
    properties = next(iter(buckets.values()))["Properties"]
    assert "LifecycleConfiguration" not in properties, properties["LifecycleConfiguration"]


def test_snapshot_retention_defaults_to_90_days():
    assert _snapshot_lambda_env(Template.from_stack(_stack()))["SNAPSHOT_RETENTION_DAYS"] == "90"


def test_snapshot_retention_is_configurable():
    template = Template.from_stack(_stack(snapshot_retention_days=30))
    assert _snapshot_lambda_env(template)["SNAPSHOT_RETENTION_DAYS"] == "30"


def test_snapshot_retention_accepts_the_upper_bound():
    template = Template.from_stack(_stack(snapshot_retention_days=36500))
    assert _snapshot_lambda_env(template)["SNAPSHOT_RETENTION_DAYS"] == "36500"


def test_snapshot_retention_none_disables_pruning():
    template = Template.from_stack(_stack(snapshot_retention_days=None))
    assert _snapshot_lambda_env(template)["SNAPSHOT_RETENTION_DAYS"] == ""


@pytest.mark.parametrize("days", [0, -1, 36501, 10 ** 6, 1.5, 90.0, True, "90"])
def test_snapshot_retention_rejects_anything_but_positive_whole_days(days):
    """The handler parses the value with int(), so anything else would fail on every scheduled run."""
    with pytest.raises(ValueError, match="snapshot_retention_days"):
        _stack(snapshot_retention_days=days)
