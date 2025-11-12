# Standard
import json
import os
from typing import Optional, List

# Installed
from constructs import Construct
from aws_cdk import (
    aws_iam as iam,
    aws_opensearchserverless as opensearch,
    aws_wafv2 as waf,
    aws_cloudfront as cf,
    aws_cloudfront_origins as origins,
    aws_lambda as lambda_,
    aws_apigateway as apigateway,
    aws_ec2 as ec2,
    aws_elasticloadbalancingv2 as elbv2,
    aws_elasticloadbalancingv2_targets as targets,
    custom_resources as cr,
    CustomResource,
    Duration,
    Fn,
    Stack
)
"""NEW FLOW USING VPCs
1. user hits custom domain in browser to access dashbaords (liberaitdc.com)
2. route53 provides the public ip of the api gateway and the request gets forwarded through there
3. a WAF checks that the user has an allowed IP 
4. allowed users get their request forwarded through a vpc link
5. a NLB recives traffic from the vpc link and provides the address of the interface endpoint
6. the user can then access the opensearch 

"""



class OLDOpenSearchServerlessConstruct(Construct):
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
            The scope in which this Construct is instantiated, usually the `self` inside a Stack.
        construct_id : str
            ID for this construct instance, e.g. "MyOpenSearchConstruct".
        collection_name : str
            The name of the OpenSearch Serverless collection
        vpc : ec2.IVpc
            The VPC to create the OpenSearch Serverless VPC endpoint in
        standby_replicas : Optional[bool]
            Indicates whether to use standby replicas for the collection. You can't update this property 
            after the collection is already created.
        allowed_ips : Optional[List[str]]
            List of CIDR ranges that are allowed to access the dashboard. If none are given deafults to public access
        """
        super().__init__(scope, construct_id)

        # Create IP Set with the allowed IP addresses
        ip_set = waf.CfnIPSet(
            self,
            "AllowedIPSet",
            addresses=allowed_ips,
            ip_address_version="IPV4",
            scope="REGIONAL",
            description="Allowed IPs for OpenSearch Serverless access",
            name="opensearch-serverless-allowed-ips",
        )

        # Create WAF Web ACL to enforce IP restrictions
        web_acl = waf.CfnWebACL(
            self,
            "OpenSearchServerlessWebACL",
            custom_response_bodies={
                "BlockedIPResponse": waf.CfnWebACL.CustomResponseBodyProperty(
                    content="Access denied: Your IP address is not authorized to access this resource.",
                    content_type="TEXT_PLAIN",
                ),
            },
            # Block all access that doesn't match any rule
            default_action=waf.CfnWebACL.DefaultActionProperty(
                block=waf.CfnWebACL.BlockActionProperty(
                    custom_response=waf.CfnWebACL.CustomResponseProperty(
                        response_code=403,
                        custom_response_body_key="BlockedIPResponse",
                    ),
                ),
            ),
            # REGIONAL scope for API Gateway (not CLOUDFRONT)
            scope="REGIONAL",
            # Enable CloudWatch metrics and web request sampling
            visibility_config=waf.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="OpenSearchServerless-WAF",
                sampled_requests_enabled=True,
            ),
            description="WAFv2 ACL for OpenSearch Serverless API Gateway",
            name="opensearch-serverless-waf",
            rules=[
                # Rule to allow only specified IPs
                waf.CfnWebACL.RuleProperty(
                    name="AllowListedIPs",
                    priority=0,
                    statement=waf.CfnWebACL.StatementProperty(
                        ip_set_reference_statement=waf.CfnWebACL.IPSetReferenceStatementProperty(
                            arn=ip_set.attr_arn,
                        ),
                    ),
                    visibility_config=waf.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="AllowListedIPsRule",
                        sampled_requests_enabled=True,
                    ),
                    action=waf.CfnWebACL.RuleActionProperty(allow={}),
                )
            ],
        )

        # Create API Gateway
        api = apigateway.RestApi(
            self, "OpenSearchServerlessAPIGateway",
            rest_api_name="OpenSearch Serverless API",
            description="API Gateway for OpenSearch Serverless access",
        )

        # Attach WAF to API Gateway
        waf.CfnWebACLAssociation(
            self,
            "WebACLAssociation",
            resource_arn=api.deployment_stage.stage_arn,
            web_acl_arn=web_acl.attr_arn,
        )

        # Create a security group for the VPC endpoint
        vpce_security_group = ec2.SecurityGroup(
            self,
            "OpenSearchServerlessVPCESecurityGroup",
            vpc=vpc,
            description="Security group for OpenSearch Serverless VPC endpoint",
            allow_all_outbound=True,
        )

        # Allow HTTPS traffic from within the VPC
        vpce_security_group.add_ingress_rule(
            peer=ec2.Peer.ipv4(vpc.vpc_cidr_block),
            connection=ec2.Port.tcp(443),
            description="Allow HTTPS from VPC",
        )

        # Create the OpenSearch Serverless VPC endpoint
        vpc_endpoint = opensearch.CfnVpcEndpoint(
            self,
            "OpenSearchServerlessVPCEndpoint",
            name="opensearch-serverless-vpce",
            vpc_id=vpc.vpc_id,
            subnet_ids=[subnet.subnet_id for subnet in vpc.private_subnets],
            security_group_ids=[vpce_security_group.security_group_id],
        )

        # Update route53 so the custom domain name being used points to the api gateway
        # Create a privatelink for serverelss in the VPC
        # configure api gateway to forrward allowd traffic to that privatelink

        # Create the serverless collection
        self.collection_name=collection_name

        # Create the policies for the collection
        encryption_policy = opensearch.CfnSecurityPolicy(
            self, "OpenSearchServerless-encryption-policy",
            name="liberaitdc-encryption-policy",
            type="encryption",
            policy=json.dumps({
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{self.collection_name}"]
                }],
                "AWSOwnedKey": True
            })
        )

        # Create network policy with VPC endpoint access
        # Note: The network policy must be created AFTER the VPC endpoint
        network_policy = opensearch.CfnSecurityPolicy(
            self, "OpenSearchServerless-network-policy",
            name="liberaitdc-network-policy",
            type="network",
            policy=json.dumps([{
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{self.collection_name}"]
                }, {
                    "ResourceType": "dashboard",
                    "Resource": [f"collection/{self.collection_name}"]
                }],
                "AllowFromPublic": False,
                "SourceVPCEs": [vpc_endpoint.attr_id]
            }])
        )

        # Add dependency to ensure VPC endpoint is created before network policy
        network_policy.add_dependency(vpc_endpoint)

        enable_replicas = "ENABLED" if standby_replicas else "DISABLED"

        # Create the new collection
        self.collection = opensearch.CfnCollection( 
            self, "OpenSearchServerlessCollection",
            name=self.collection_name,
            type="SEARCH",
            standby_replicas=enable_replicas,
        )
        
        # attach policies to collection
        self.collection.add_dependency(encryption_policy)
        self.collection.add_dependency(network_policy)

        self.domain_endpoint = self.collection.attr_collection_endpoint
        self.domain_name = collection_name
        self.domain_arn = self.collection.attr_arn


class OpenSearchServerlessConstruct(Construct):
    """
    New OpenSearch Serverless construct with VPC-based access control.
    
    Architecture Flow:
    1. User hits API Gateway public endpoint (or custom domain via Route53)
    2. WAF checks IP allowlist
    3. Allowed requests forwarded through VPC Link
    4. NLB receives traffic and routes to VPC Interface Endpoint
    5. User accesses OpenSearch Serverless collection
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
            List of CIDR ranges allowed to access the dashboard
        """
        super().__init__(scope, construct_id)
        
        self.collection_name = collection_name
        self.vpc = vpc
        self.allowed_ips = allowed_ips
        self.standby_replicas = standby_replicas
        
        # Create resources in order of dependencies
        self._create_opensearch_collection()
        self._create_vpc_endpoint()
        self._create_network_load_balancer()
        self._create_vpc_link()
        self._create_waf()
        self._create_api_gateway()
    
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
        
        # Create VPC endpoint in private subnets
        self.vpc_endpoint = opensearch.CfnVpcEndpoint(
            self,
            "VPCEndpoint",
            name=f"{self.collection_name}-vpce",
            vpc_id=self.vpc.vpc_id,
            subnet_ids=[subnet.subnet_id for subnet in self.vpc.private_subnets],
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
        """Create NLB to front the VPC endpoint."""
        # Create NLB in private subnets
        self.nlb = elbv2.NetworkLoadBalancer(
            self,
            "NLB",
            vpc=self.vpc,
            internet_facing=False,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
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
    
    def _create_vpc_link(self) -> None:
        """Create VPC Link for API Gateway integration."""
        self.vpc_link = apigateway.VpcLink(
            self,
            "VPCLink",
            targets=[self.nlb],
            description=f"VPC Link for {self.collection_name} OpenSearch Serverless",
        )
    
    def _create_waf(self) -> None:
        """Create WAF with IP restrictions."""
        # Create IP Set with allowed IPs
        self.ip_set = waf.CfnIPSet(
            self,
            "AllowedIPSet",
            addresses=self.allowed_ips,
            ip_address_version="IPV4",
            scope="REGIONAL",
            description=f"Allowed IPs for {self.collection_name} OpenSearch Serverless access",
            name=f"{self.collection_name}-allowed-ips",
        )
        
        # Create Web ACL with IP allowlist rule
        self.web_acl = waf.CfnWebACL(
            self,
            "WebACL",
            custom_response_bodies={
                "BlockedIPResponse": waf.CfnWebACL.CustomResponseBodyProperty(
                    content="Access denied: Your IP address is not authorized to access this resource.",
                    content_type="TEXT_PLAIN",
                ),
            },
            default_action=waf.CfnWebACL.DefaultActionProperty(
                block=waf.CfnWebACL.BlockActionProperty(
                    custom_response=waf.CfnWebACL.CustomResponseProperty(
                        response_code=403,
                        custom_response_body_key="BlockedIPResponse",
                    ),
                ),
            ),
            scope="REGIONAL",
            visibility_config=waf.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=f"{self.collection_name}-WAF",
                sampled_requests_enabled=True,
            ),
            description=f"WAFv2 ACL for {self.collection_name} OpenSearch Serverless API Gateway",
            name=f"{self.collection_name}-waf",
            rules=[
                waf.CfnWebACL.RuleProperty(
                    name="AllowListedIPs",
                    priority=0,
                    statement=waf.CfnWebACL.StatementProperty(
                        ip_set_reference_statement=waf.CfnWebACL.IPSetReferenceStatementProperty(
                            arn=self.ip_set.attr_arn,
                        ),
                    ),
                    visibility_config=waf.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="AllowListedIPsRule",
                        sampled_requests_enabled=True,
                    ),
                    action=waf.CfnWebACL.RuleActionProperty(allow={}),
                )
            ],
        )
    
    def _create_api_gateway(self) -> None:
        """Create API Gateway with VPC integration."""
        # Create REST API
        self.api = apigateway.RestApi(
            self,
            "APIGateway",
            rest_api_name=f"{self.collection_name} OpenSearch Serverless API",
            description=f"API Gateway for {self.collection_name} OpenSearch Serverless access",
            endpoint_types=[apigateway.EndpointType.REGIONAL],
        )
        
        # Get the collection endpoint hostname (remove https://)
        collection_endpoint_host = Fn.select(1, Fn.split("//", self.domain_endpoint))
        
        # Create VPC integration
        integration = apigateway.Integration(
            type=apigateway.IntegrationType.HTTP_PROXY,
            integration_http_method="ANY",
            options=apigateway.IntegrationOptions(
                connection_type=apigateway.ConnectionType.VPC_LINK,
                vpc_link=self.vpc_link,
                request_parameters={
                    "integration.request.header.Host": f"'{collection_endpoint_host}'",
                    "integration.request.path.proxy": "method.request.path.proxy",
                },
            ),
            uri=f"https://{collection_endpoint_host}/{{proxy}}",
        )
        
        # Create proxy resource for catch-all routing
        proxy_resource = self.api.root.add_resource("{proxy+}")
        
        # Add ANY method to proxy resource with VPC integration
        proxy_resource.add_method(
            "ANY",
            integration,
            request_parameters={
                "method.request.path.proxy": True,
            },
        )
        
        # Also add ANY method to root for non-proxy requests
        root_integration = apigateway.Integration(
            type=apigateway.IntegrationType.HTTP_PROXY,
            integration_http_method="ANY",
            options=apigateway.IntegrationOptions(
                connection_type=apigateway.ConnectionType.VPC_LINK,
                vpc_link=self.vpc_link,
                request_parameters={
                    "integration.request.header.Host": f"'{collection_endpoint_host}'",
                },
            ),
            uri=f"https://{collection_endpoint_host}/",
        )
        
        self.api.root.add_method("ANY", root_integration)
        
        # Associate WAF with API Gateway stage
        waf.CfnWebACLAssociation(
            self,
            "WebACLAssociation",
            resource_arn=self.api.deployment_stage.stage_arn,
            web_acl_arn=self.web_acl.attr_arn,
        )
        
        # Expose API Gateway URL
        self.api_gateway_url = self.api.url

       