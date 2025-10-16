# Standard
import json
from typing import Optional, List

# Installed
from constructs import Construct
from aws_cdk import (
    aws_route53 as route53,
    aws_certificatemanager as acm,
    RemovalPolicy,
    Environment,
    aws_iam as iam,
    aws_opensearchserverless as opensearch,
    aws_ec2 as ec2
)


class OpenSearchServerlessConstruct(Construct):
    def __init__(
        self, 
        scope: Construct,
        construct_id: str,
        vpc: ec2.IVpc,
        opensearch_ip_access_range: Optional[List[str]] = ["0.0.0.0/0"],
    ) -> None:
        """
        Construct init.
        Parameters
        ----------
        scope : Construct
            The scope in which this Construct is instantiated, usually the `self` inside a Stack.
        construct_id : str
            ID for this construct instance, e.g. "MyOpenSearchConstruct".
        vpc: ec2.vpc
    
            TODO ADD PARAM DESCRIPTION HERE
        
        opensearch_ip_access_range : List[str], optional
            IP CIDR block on which to allow OpenSearch domain access (e.g. for security purposes).
            Default is 0.0.0.0/0 (open everywhere). Note: leaving this unchanged will raise a warning that your cluster
            is available to the public internet.
        """

        super().__init__(scope, construct_id)

        collection_name="liberaITDC-collection"
        self.vpc = vpc
        self.allowed_cidrs = opensearch_ip_access_range


        # Create a security group for the OpenSearch Serverless VPC endpoint
        endpoint_sg = ec2.SecurityGroup(
            self, "OpenSearchEndpointSG",
            vpc=self.vpc,
            allow_all_outbound=True,
        )

        # Add ingress rules for each allowed CIDR range
        for cidr in self.allowed_cidrs:
            endpoint_sg.add_ingress_rule(
                peer=ec2.Peer.ipv4(cidr),
                connection=ec2.Port.tcp(443),
                description=f"Allow HTTPS from {cidr}"
            )

        # Create the VPC endpoint for OpenSearch Serverless
        self.vpc_endpoint = ec2.InterfaceVpcEndpoint(
            self, "OpenSearchServerlessVpcEndpoint",
            vpc=self.vpc,
            service=ec2.InterfaceVpcEndpointAwsService.OPENSEARCH_SERVERLESS,
            subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_ISOLATED),  # Use isolated subnets for private access
            security_groups=[endpoint_sg],
            private_dns_enabled=True,  # Enables private DNS resolution within the VPC TODO is this needed?
        )

        # Create the policies for the collection
        encryption_policy = opensearch.CfnSecurityPolicy(
            self, "OpenSearchServerless-encryption-policy",
            name="liberaITDC-encryption-policy",
            type="encryption",
            policy=json.dumps([
                {
                    "ResourceType": "collection",
                    "Resource": [f"collection/{collection_name}"],
                    "AWSOwnedKey": True
                }
            ])
        )

        network_rules = []

        # Add rules for private VPC access (collection and dashboard)
        network_rules.append({
            "ResourceType": "collection",
            "Resource": [f"collection/{collection_name}"],
            "AllowFromPublic": False,
            "SourceVPCEs": [self.vpc_endpoint.vpc_endpoint_id]
        })
        network_rules.append({
            "ResourceType": "dashboard",
            "Resource": [f"collection/{collection_name}"],
            "AllowFromPublic": False,
            "SourceVPCEs": [self.vpc_endpoint.vpc_endpoint_id]
        })

        # Add rules for public CIDR-restricted access (if CIDRs are provided)
        network_rules.append({
            "ResourceType": "collection",
            "Resource": [f"collection/{collection_name}"],
            "AllowFromPublic": True,
            "SourceCIDRs": self.allowed_cidrs
        })
        network_rules.append({
            "ResourceType": "dashboard",
            "Resource": [f"collection/{collection_name}"],
            "AllowFromPublic": True,
            "SourceCIDRs": self.allowed_cidrs
        })

        network_policy = opensearch.CfnSecurityPolicy(
            self, "OpenSearchServerless-network-policy",
            name="liberaITDC-network-policy",
            type="network",
            policy=json.dumps(network_rules)
        )

        access_policy = opensearch.CfnAccessPolicy(
            self, "OpenSearchServerless-access-policy",
            name= "liberaITDC-access-policy",
            type="data",
            policy=json.dumps({
                "Rules": [{
                    "ResourceType": "index",
                    "Resource": [f"index/{collection_name}/*"],
                    "Permission": ["aoss:CreateIndex", "aoss:ReadDocument", "aoss:WriteDocument"] #TODO might need to add some more privileges 
                }],
                "Principal":[iam.AnyPrincipal()] #TODO probably need to restrict this further
            })
        )

        # Create the new collection
        self.collection = opensearch.CfnCollection( 
            self, "OpenSearchServerlessCollection",
            name=collection_name,
            type="SEARCH",
        )
        
        # attach policies to collection
        self.collection.add_dependency(encryption_policy)
        self.collection.add_dependency(network_policy)
        self.collection.add_dependency(access_policy)
