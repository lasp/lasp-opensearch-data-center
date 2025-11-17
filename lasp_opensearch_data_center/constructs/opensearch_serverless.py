# Standard
import json
import os
from typing import Optional, List

# Installed
from constructs import Construct
from aws_cdk import (
    aws_iam as iam,
    aws_opensearchserverless as opensearch,
    aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elbv2,
    aws_lambda as lambda_,
    custom_resources as cr,
    CustomResource,
    Duration,
    Stack,
    Fn
)
"""NEW FLOW USING VPCs WITH SECURITY GROUP IP FILTERING
1. User hits public NLB endpoint (or custom domain via Route53)
2. NLB security group checks if source IP is allowed
3. Allowed traffic forwarded to VPC endpoint
4. VPC endpoint routes to OpenSearch Serverless collection
5. User accesses OpenSearch dashboards

"""


class OpenSearchServerlessConstruct(Construct):
    """
    OpenSearch Serverless construct with security group-based IP access control.
    
    Architecture Flow:
    1. User hits public NLB endpoint (or custom domain via Route53)
    2. NLB security group checks if source IP is allowed
    3. Allowed traffic forwarded to VPC endpoint
    4. VPC endpoint routes to OpenSearch Serverless collection
    5. User accesses OpenSearch dashboards
    """
    
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        collection_name: str,
        vpc: ec2.IVpc,
        standby_replicas: Optional[bool] = True,
        allowed_ips: Optional[List[str]] = ["0.0.0.0/0"]
    ) -> None:
        """
        Construct init.
        
        Parameters
        ----------
        scope : Construct
            The scope in which this Construct is instantiated
        construct_id : str
            ID for this construct instance
        collection_name : str
            The name of the OpenSearch Serverless collection
        vpc : ec2.IVpc
            The VPC to create the OpenSearch Serverless VPC endpoint in
        standby_replicas : Optional[bool]
            Whether to use standby replicas for the collection
        allowed_ips : Optional[List[str]]
            List of CIDR ranges allowed to access the dashboard via NLB
        """
        super().__init__(scope, construct_id)
        
        self.collection_name = collection_name
        self.vpc = vpc
        self.allowed_ips = allowed_ips
        self.standby_replicas = standby_replicas
        
        # Create resources in order of dependencies
        self._create_opensearch_collection()
        self._create_private_subnets()
        self._create_vpc_endpoint()
        self._create_network_load_balancer()
    
    def _create_opensearch_collection(self) -> None:
        """Create OpenSearch Serverless collection with policies."""
        # Create encryption policy
        encryption_policy = opensearch.CfnSecurityPolicy(
            self,
            "EncryptionPolicy",
            name=f"{self.collection_name}-encryption-policy",
            type="encryption",
            policy=json.dumps({
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{self.collection_name}"]
                }],
                "AWSOwnedKey": True
            })
        )
        
        # Note: Network policy will be created after VPC endpoint
        # Data access policy - grants admin access to the account root
        # In production, you should specify specific IAM roles/users
        data_access_policy = opensearch.CfnAccessPolicy(
            self,
            "DataAccessPolicy",
            name=f"{self.collection_name}-data-access-policy",
            type="data",
            policy=json.dumps([{
                "Rules": [
                    {
                        "ResourceType": "collection",
                        "Resource": [f"collection/{self.collection_name}"],
                        "Permission": [
                            "aoss:CreateCollectionItems",
                            "aoss:DeleteCollectionItems",
                            "aoss:UpdateCollectionItems",
                            "aoss:DescribeCollectionItems"
                        ]
                    },
                    {
                        "ResourceType": "index",
                        "Resource": [f"index/{self.collection_name}/*"],
                        "Permission": [
                            "aoss:CreateIndex",
                            "aoss:DeleteIndex",
                            "aoss:UpdateIndex",
                            "aoss:DescribeIndex",
                            "aoss:ReadDocument",
                            "aoss:WriteDocument"
                        ]
                    }
                ],
                "Principal": [f"arn:aws:iam::{Stack.of(self).account}:root"]
            }])
        )
        
        enable_replicas = "ENABLED" if self.standby_replicas else "DISABLED"
        
        # Create the collection
        self.collection = opensearch.CfnCollection(
            self,
            "Collection",
            name=self.collection_name,
            type="SEARCH",
            standby_replicas=enable_replicas,
        )
        
        # Add dependencies
        self.collection.add_dependency(encryption_policy)
        self.collection.add_dependency(data_access_policy)
        
        # Expose collection attributes
        self.domain_endpoint = self.collection.attr_collection_endpoint
        self.domain_name = self.collection_name
        self.domain_arn = self.collection.attr_arn
        self.collection_id = self.collection.attr_id
    
    def _create_private_subnets(self) -> None:
        """Create dedicated private subnets for OpenSearch Serverless VPC endpoint."""
        # Get the VPC's availability zones
        availability_zones = self.vpc.availability_zones
        
        # Create private subnets using CfnSubnet for proper CIDR handling
        # Use higher indices (200-201) to avoid conflicts with existing subnets
        self.opensearch_subnets = []
        for i, az in enumerate(availability_zones[:2]):
            subnet = ec2.CfnSubnet(
                self,
                f"OpenSearchPrivateSubnet{i+1}",
                availability_zone=az,
                vpc_id=self.vpc.vpc_id,
                cidr_block=Fn.select(200 + i, Fn.cidr(self.vpc.vpc_cidr_block, 256, "8")),
                map_public_ip_on_launch=False,
                tags=[{
                    "key": "Name",
                    "value": f"OpenSearch-Private-Subnet-{i+1}"
                }]
            )
            self.opensearch_subnets.append(subnet)
    
    def _create_vpc_endpoint(self) -> None:
        """Create VPC Interface Endpoint for OpenSearch Serverless."""
        # Create security group for VPC endpoint
        self.vpce_security_group = ec2.SecurityGroup(
            self,
            "VPCEndpointSecurityGroup",
            vpc=self.vpc,
            description="Security group for OpenSearch Serverless VPC endpoint",
            allow_all_outbound=True,
        )
        
        # Allow HTTPS traffic from within the VPC
        self.vpce_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(self.vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(443),
            description="Allow HTTPS from VPC",
        )
        
        # Create VPC endpoint in the dedicated private subnets
        self.vpc_endpoint = opensearch.CfnVpcEndpoint(
            self,
            "VPCEndpoint",
            name=f"{self.collection_name}-vpce",
            vpc_id=self.vpc.vpc_id,
            subnet_ids=[subnet.ref for subnet in self.opensearch_subnets],
            security_group_ids=[self.vpce_security_group.security_group_id],
        )
        
        # Create network policy AFTER VPC endpoint
        network_policy = opensearch.CfnSecurityPolicy(
            self,
            "NetworkPolicy",
            name=f"{self.collection_name}-network-policy",
            type="network",
            policy=json.dumps([{
                "Rules": [
                    {
                        "ResourceType": "collection",
                        "Resource": [f"collection/{self.collection_name}"]
                    },
                    {
                        "ResourceType": "dashboard",
                        "Resource": [f"collection/{self.collection_name}"]
                    }
                ],
                "AllowFromPublic": False,
                "SourceVPCEs": [self.vpc_endpoint.attr_id]
            }])
        )
        
        # Ensure proper creation order
        network_policy.add_dependency(self.vpc_endpoint)
        self.collection.add_dependency(network_policy)
        
        self.vpc_endpoint_id = self.vpc_endpoint.attr_id
    
    def _create_network_load_balancer(self) -> None:
        """Create public NLB with security group IP filtering to front the VPC endpoint."""
        # Create security group for NLB with IP-based access control
        self.nlb_security_group = ec2.SecurityGroup(
            self,
            "NLBSecurityGroup",
            vpc=self.vpc,
            description=f"Security group for {self.collection_name} OpenSearch NLB with IP allowlist",
            allow_all_outbound=True,
        )
        
        # Add ingress rules for each allowed IP/CIDR
        for idx, ip_cidr in enumerate(self.allowed_ips):
            # Add /32 suffix if not already in CIDR notation
            if '/' not in ip_cidr:
                ip_cidr = f"{ip_cidr}/32"
            
            self.nlb_security_group.add_ingress_rule(
                peer=ec2.Peer.ipv4(ip_cidr),
                connection=ec2.Port.tcp(443),
            )
        
        # Create public NLB in public subnets
        self.nlb = elbv2.NetworkLoadBalancer(
            self,
            "NLB",
            vpc=self.vpc,
            internet_facing=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_groups=[self.nlb_security_group],
        )
        
        # Create target group for IP targets
        self.target_group = elbv2.NetworkTargetGroup(
            self,
            "TargetGroup",
            vpc=self.vpc,
            port=443,
            protocol=elbv2.Protocol.TCP,
            target_type=elbv2.TargetType.IP,
            health_check=elbv2.HealthCheck(
                protocol=elbv2.Protocol.TCP,
                port="443",
            ),
            deregistration_delay=Duration.seconds(30),
        )
        
        # Add listener
        self.nlb.add_listener(
            "Listener",
            port=443,
            protocol=elbv2.Protocol.TCP,
            default_target_groups=[self.target_group],
        )
        
        # Create Lambda function to resolve VPC endpoint IPs and register them as targets
        # This is needed because VPC endpoint IPs are not directly available
        resolve_ips_lambda = lambda_.Function(
            self,
            "ResolveVPCEndpointIPs",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="index.handler",
            code=lambda_.Code.from_inline("""
import json
import boto3
import cfnresponse

ec2 = boto3.client('ec2')
elbv2 = boto3.client('elbv2')

def handler(event, context):
    try:
        print(f"Event: {json.dumps(event)}")
        
        if event['RequestType'] == 'Delete':
            cfnresponse.send(event, context, cfnresponse.SUCCESS, {})
            return
        
        vpc_endpoint_id = event['ResourceProperties']['VpcEndpointId']
        target_group_arn = event['ResourceProperties']['TargetGroupArn']
        
        # Get network interfaces for the VPC endpoint
        response = ec2.describe_network_interfaces(
            Filters=[
                {'Name': 'vpc-endpoint-id', 'Values': [vpc_endpoint_id]}
            ]
        )
        
        ips = [ni['PrivateIpAddress'] for ni in response['NetworkInterfaces']]
        print(f"Found IPs: {ips}")
        
        if not ips:
            raise Exception("No IPs found for VPC endpoint")
        
        # Register IPs as targets
        targets = [{'Id': ip, 'Port': 443} for ip in ips]
        elbv2.register_targets(
            TargetGroupArn=target_group_arn,
            Targets=targets
        )
        
        cfnresponse.send(event, context, cfnresponse.SUCCESS, {
            'IPs': ','.join(ips)
        })
    except Exception as e:
        print(f"Error: {str(e)}")
        cfnresponse.send(event, context, cfnresponse.FAILED, {
            'Error': str(e)
        })
"""),
            timeout=Duration.seconds(60),
        )
        
        # Grant permissions to the Lambda
        resolve_ips_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "ec2:DescribeNetworkInterfaces",
                ],
                resources=["*"],
            )
        )
        
        resolve_ips_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=[
                    "elasticloadbalancing:RegisterTargets",
                    "elasticloadbalancing:DeregisterTargets",
                ],
                resources=[self.target_group.target_group_arn],
            )
        )
        
        # Create custom resource provider
        provider = cr.Provider(
            self,
            "VPCEndpointIPsProvider",
            on_event_handler=resolve_ips_lambda,
        )
        
        # Create custom resource to trigger the Lambda
        custom_resource = CustomResource(
            self,
            "VPCEndpointIPsResolver",
            service_token=provider.service_token,
            properties={
                "VpcEndpointId": self.vpc_endpoint.attr_id,
                "TargetGroupArn": self.target_group.target_group_arn,
            },
        )
        
        # Ensure VPC endpoint is created before resolving IPs
        custom_resource.node.add_dependency(self.vpc_endpoint)
        custom_resource.node.add_dependency(self.target_group)
        
        # Update VPC endpoint security group to allow traffic from NLB
        self.vpce_security_group.add_ingress_rule(
            peer=ec2.Peer.security_group_id(self.nlb_security_group.security_group_id),
            connection=ec2.Port.tcp(443),
            description="Allow HTTPS from NLB",
        )
        
        # Expose NLB DNS name as the public endpoint
        self.nlb_dns_name = self.nlb.load_balancer_dns_name