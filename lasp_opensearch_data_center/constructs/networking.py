"""Shared networking resources"""
# Installed
from constructs import Construct
from aws_cdk import Environment, aws_ec2 as ec2, aws_route53 as r53


class NetworkingComponentsConstruct(Construct):
    """General networking resources for Opensearch Data Center"""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        self.vpc = ec2.Vpc(
            self,
            "DataCenterVpc",
            # TODO: Re-enable NAT Gateways when we need them
            nat_gateways=0,
            # 65,536 IPs in /16
            ip_addresses=ec2.IpAddresses.cidr("10.1.0.0/16"),
            # Instances launched in the VPC (can) get public DNS hostnames
            enable_dns_hostnames=True,
            # DNS resolution is supported for the VPC
            enable_dns_support=True,
            # There are charges to move data across AZs so
            # we let's start with 2 and increase as needed
            max_azs=2,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="PublicSubnet",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    # 254 IPs in a /24
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    subnet_type=ec2.SubnetType.PRIVATE_ISOLATED,
                    name="IsolatedSubnet",
                    # 254 IPs in a /24
                    cidr_mask=24,
                ),
            ],
        )
