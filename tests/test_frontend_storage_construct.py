"""Tests for the site content bucket that FrontendStorageConstruct creates"""
# Installed
import aws_cdk as cdk
from aws_cdk.assertions import Template
import pytest
# Local
from lasp_opensearch_data_center.constructs.frontend_storage import FrontendStorageConstruct
from tests.construct_helpers import ACCOUNT, hosted_zone_context, only_resource


def _template(domain_name: str = "example.com", region: str = "us-east-1") -> Template:
    """Synthesize the construct, answering its hosted zone lookup from context instead of AWS"""
    environment = cdk.Environment(account=ACCOUNT, region=region)
    app = cdk.App(context=hosted_zone_context(domain_name, region))
    stack = cdk.Stack(app, "TestStack", env=environment)
    FrontendStorageConstruct(stack, "FrontendStorage", environment=environment, domain_name=domain_name)
    return Template.from_stack(stack)


@pytest.mark.parametrize("domain_name", ["example.com", "dev.data.example.org"])
def test_bucket_is_named_after_the_site_domain(domain_name):
    """The bucket takes the site's domain as its name, which keeps it unique per data center."""
    bucket = only_resource(_template(domain_name=domain_name), "AWS::S3::Bucket")
    assert bucket["Properties"]["BucketName"] == domain_name


def test_bucket_disables_acls_and_is_deleted_with_the_stack():
    """Access is by bucket policy only (ACLs off), and an empty bucket goes when the stack does."""
    bucket = only_resource(_template(), "AWS::S3::Bucket")
    assert bucket["Properties"] == {
        "BucketName": "example.com",
        "OwnershipControls": {"Rules": [{"ObjectOwnership": "BucketOwnerEnforced"}]},
    }
    assert bucket["DeletionPolicy"] == "Delete"
    assert bucket["UpdateReplacePolicy"] == "Delete"


def test_refuses_any_region_but_us_east_1():
    """CloudFront's WAF must live in us-east-1, so the construct refuses to be built anywhere else."""
    with pytest.raises(ValueError, match="us-east-1"):
        _template(region="us-west-2")
