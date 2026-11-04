"""Tests for the VPC that NetworkingComponentsConstruct creates"""
# Standard
from typing import Tuple
# Installed
import aws_cdk as cdk
from aws_cdk.assertions import Template
import pytest
# Local
from lasp_opensearch_data_center.constructs.networking import NetworkingComponentsConstruct
from tests.construct_helpers import ACCOUNT, only_resource, only_resource_id

REGION = "us-west-2"
# The region offers three zones, so a test can see the construct use only two of them
AVAILABILITY_ZONES = ["us-west-2a", "us-west-2b", "us-west-2c"]


def _build() -> Tuple[cdk.Stack, NetworkingComponentsConstruct]:
    """Synthesize the construct in a stack whose zone lookup is answered from context, not AWS"""
    app = cdk.App(context={f"availability-zones:account={ACCOUNT}:region={REGION}": AVAILABILITY_ZONES})
    stack = cdk.Stack(app, "TestStack", env=cdk.Environment(account=ACCOUNT, region=REGION))
    networking = NetworkingComponentsConstruct(stack, "Networking")
    return stack, networking


@pytest.fixture(scope="module")
def built() -> Tuple[cdk.Stack, NetworkingComponentsConstruct, Template]:
    """The construct, its stack and the stack's template"""
    stack, networking = _build()
    return stack, networking, Template.from_stack(stack)


def test_vpc_uses_10_1_0_0_16_with_dns(built):
    """The VPC's address range and DNS settings are what the rest of the data center is laid out against."""
    _, _, template = built
    vpc = only_resource(template, "AWS::EC2::VPC")
    properties = vpc["Properties"]
    assert properties["CidrBlock"] == "10.1.0.0/16"
    assert properties["EnableDnsHostnames"] is True
    assert properties["EnableDnsSupport"] is True


def test_one_public_and_one_isolated_24_subnet_in_each_of_two_zones(built):
    """Two zones limit cross-zone data charges, and each zone gets one public and one isolated /24 subnet."""
    _, _, template = built
    subnets = template.find_resources("AWS::EC2::Subnet")
    layout = set()
    for subnet in subnets.values():
        properties = subnet["Properties"]
        tags = {tag["Key"]: tag["Value"] for tag in properties["Tags"]}
        layout.add(
            (
                tags["aws-cdk:subnet-type"],
                properties["AvailabilityZone"],
                properties["CidrBlock"],
                properties["MapPublicIpOnLaunch"],
            )
        )
    assert len(subnets) == 4
    assert layout == {
        ("Public", "us-west-2a", "10.1.0.0/24", True),
        ("Public", "us-west-2b", "10.1.1.0/24", True),
        ("Isolated", "us-west-2a", "10.1.2.0/24", False),
        ("Isolated", "us-west-2b", "10.1.3.0/24", False),
    }


def test_no_nat_gateways(built):
    """NAT gateways are billed by the hour and the construct turns them off until they are needed."""
    _, _, template = built
    template.resource_count_is("AWS::EC2::NatGateway", 0)
    template.resource_count_is("AWS::EC2::EIP", 0)


def test_only_public_subnets_route_to_the_internet(built):
    """The isolated subnets have no route out; only the public subnets' route tables send 0.0.0.0/0 to the gateway."""
    stack, networking, template = built
    gateway_id = only_resource_id(template, "AWS::EC2::InternetGateway")
    # Each route table id resolves to {"Ref": <logical id of the route table>}
    public_route_tables = [stack.resolve(subnet.route_table.route_table_id) for subnet in networking.vpc.public_subnets]
    routes = [route["Properties"] for route in template.find_resources("AWS::EC2::Route").values()]
    assert len(public_route_tables) == 2
    assert len(routes) == 2
    for route in routes:
        assert route["DestinationCidrBlock"] == "0.0.0.0/0"
        assert route["GatewayId"] == {"Ref": gateway_id}
        assert route["RouteTableId"] in public_route_tables
    assert routes[0]["RouteTableId"] != routes[1]["RouteTableId"]

