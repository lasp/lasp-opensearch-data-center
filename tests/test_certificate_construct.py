"""Tests for the wildcard certificate that CertificateConstruct creates"""
# Standard
from typing import Tuple
# Installed
import aws_cdk as cdk
from aws_cdk.assertions import Template
import pytest
# Local
from lasp_opensearch_data_center.constructs.certificate import CertificateConstruct
from tests.construct_helpers import ACCOUNT, HOSTED_ZONE_ID, hosted_zone_context, only_resource, only_resource_id

REGION = "us-west-2"


def _build(domain_name: str) -> Tuple[cdk.Stack, CertificateConstruct]:
    """Synthesize the construct, answering its hosted zone lookup from context instead of AWS"""
    app = cdk.App(context=hosted_zone_context(domain_name, REGION))
    stack = cdk.Stack(app, "TestStack", env=cdk.Environment(account=ACCOUNT, region=REGION))
    certificate = CertificateConstruct(stack, "Certificate", domain_name=domain_name)
    return stack, certificate


@pytest.mark.parametrize("domain_name", ["example.com", "dev.data.example.org"])
def test_wildcard_certificate_for_the_domain_validated_through_its_zone(domain_name):
    """The certificate covers *.<domain> (search., dynamoquery. and the like) and is validated by DNS in that zone."""
    stack, _ = _build(domain_name)
    certificate = only_resource(Template.from_stack(stack), "AWS::CertificateManager::Certificate")
    properties = certificate["Properties"]
    assert properties["DomainName"] == f"*.{domain_name}"
    assert properties["ValidationMethod"] == "DNS"
    assert properties["DomainValidationOptions"] == [
        {"DomainName": f"*.{domain_name}", "HostedZoneId": HOSTED_ZONE_ID}
    ]
    # The wildcard alone: no other names ride on this certificate
    assert "SubjectAlternativeNames" not in properties


def test_exposes_the_looked_up_zone_and_the_certificate():
    """Callers pass these attributes to the OpenSearch construct, so they must be the zone and certificate made here."""
    stack, construct = _build("example.com")
    template = Template.from_stack(stack)
    assert construct.hosted_zone.zone_name == "example.com"
    assert stack.resolve(construct.hosted_zone.hosted_zone_id) == HOSTED_ZONE_ID
    certificate_id = only_resource_id(template, "AWS::CertificateManager::Certificate")
    assert stack.resolve(construct.account_cert.certificate_arn) == {"Ref": certificate_id}


def test_exports_the_certificate_arn_as_account_certificate_arn():
    """Other stacks import the certificate by this export name, so renaming it breaks them."""
    stack, _ = _build("example.com")
    template = Template.from_stack(stack)
    certificate_id = only_resource_id(template, "AWS::CertificateManager::Certificate")
    outputs = template.find_outputs("*")
    assert list(outputs.values()) == [{"Value": {"Ref": certificate_id}, "Export": {"Name": "accountCertificateArn"}}]
