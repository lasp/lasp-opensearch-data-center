# Standard
import json
from typing import Optional, List

# Installed
from constructs import Construct
from aws_cdk import (
    aws_route53 as route53,
    aws_route53_targets as targets,
    aws_certificatemanager as acm,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_iam as iam,
    aws_opensearchserverless as opensearch,
    aws_ec2 as ec2
)


class OpenSearchServerlessConstruct(Construct):
    def __init__(
        self, 
        scope: Construct,
        construct_id: str,
        collection_name: str,
        domain_name: str,
        hosted_zone: route53.IHostedZone,
        certificate_arn: str,
        allowed_ip_ranges: Optional[List[str]] = None,
        standby_replicas: Optional[bool] = True
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
        domain_name : str,
            The domain that will be used to access the OpenSearch Dashboard
        hosted_zone : route53.IHostedZone
            The Route53 hosted zone for your custom domain
        certificate_arn : str
           The ARN of the ACM certificate (must be in us-east-1)
        allowed_ip_ranges : Optional[List[str]]
            List of IP ranges in CIDR notation that are allowed to access the dashboard.
            If None, all IPs are allowed.
        standby_replicas : Optional[bool]
            Indicates whether to use standby replicas for the collection. You can't update this property 
            after the collection is already created.
        """


        #Custom domain and IP restricton plan
        # need r53, domain name, and acm cert
        # create cloud frount dist
        # attach AWS WAF IP restriction
        # add an a name record to R53 and point it to cloud front
        super().__init__(scope, construct_id)



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
                "AllowFromPublic": True
            }])
        )

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
        self.domain_name = domain_name
        self.domain_arn = self.collection.attr_arn
        self.dashboard_endpoint = self.collection.attr_dashboard_endpoint


        # Track principals that need access
        self.read_write_principals: List[str] = []
        self.read_write_principals.append("arn:aws:iam::983496429036:root") #TODO for testing, need a better way to allow people with AWS conolse access to view the serverless cluster (and the dashboard)


        # setup up networking stuff
        ssl_cert =  acm.Certificate.from_certificate_arn(
            self, "DashboardCertificate", certificate_arn
        )

        cf_dist = cloudfront.Distribution(
            self, "DashboardDistribution",
            default_behavior=cloudfront.BehaviorOptions(
                origin=origins.HttpOrigin(
                    domain_name=self.dashboard_endpoint.replace("https://", "").replace("http://", ""),
                    origin_ssl_protocols=[cloudfront.OriginSslPolicy.TLS_V1_2],
                    protocol_policy=cloudfront.OriginProtocolPolicy.HTTPS_ONLY
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                allowed_methods=cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            ),
            domain_names=[str(domain_name)],
            certificate=ssl_cert,
            default_root_object="",  # dashboard root handled by OpenSearch
        )

        # Create a Route 53 alias record for your custom domain
        route53.ARecord(
            self, "DashboardAliasRecord",
            zone=hosted_zone,
            record_name=domain_name.split(".")[0],  #TODO this assume the endpoint for the dashboard is "dashboard.customdomain.com"
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(cf_dist)
            ),
        )
    def grant_read_write(self, identity: iam.IGrantable) -> None:
        """
        Grant read and write access to indexes in the collection.
        This is the main method for granting Lambda functions access.
        
        Parameters
        ----------
        identity : iam.IGrantable
            The Lambda function or other IAM principal to grant access to
        """
        # Extract the role ARN from the grantable identity
        principal_arn = self._get_principal_arn(identity)
        
        # Add to our tracking list
        if principal_arn not in self.read_write_principals:
            self.read_write_principals.append(principal_arn)
        
        # Grant the IAM principal permission to use AOSS APIs
        identity.grant_principal.add_to_principal_policy(
            iam.PolicyStatement(
                actions=[
                    "aoss:APIAccessAll",  # General API access
                    "aoss:*"  # All OpenSearch Serverless actions
                ],
                resources=[self.collection.attr_arn],
                effect=iam.Effect.ALLOW
            )
        )
    
    def create_access_policy(self) -> None:
        """
        Create and attach the data access policy for the collection.
        This should be called after all principals have been granted access via grant_read_write().
        The policy will be created with all principals that were added to read_write_principals.
        """
        if not self.read_write_principals:
            raise ValueError(
                "No principals have been granted access. Call grant_read_write() before creating the access policy."
            )
        
        access_policy = opensearch.CfnAccessPolicy(
            self, "OpenSearchServerless-access-policy",
            name="liberaitdc-access-policy",
            type="data",
            policy=json.dumps([{
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{self.collection_name}"],
                    "Permission": [
                        "aoss:CreateCollectionItems",
                        "aoss:UpdateCollectionItems",
                        "aoss:DescribeCollectionItems"
                    ]
                }, {
                    "ResourceType": "index",
                    "Resource": [f"index/{self.collection_name}/*"],
                    "Permission": [
                        "aoss:CreateIndex",
                        "aoss:ReadDocument",
                        "aoss:WriteDocument",
                        "aoss:UpdateIndex",
                        "aoss:DeleteIndex",
                        "aoss:DescribeIndex"
                    ]
                }],
                "Principal": self.read_write_principals
            }])
        )
        
        # Make collection depend on access policy
        self.collection.add_dependency(access_policy)
    
    def _get_principal_arn(self, identity: iam.IGrantable) -> str:
        """
        Extract the ARN from an IGrantable identity.
        
        Parameters
        ----------
        identity : iam.IGrantable
            The identity to extract the ARN from
            
        Returns
        -------
        str
            The ARN of the principal (role or user)
        """
        principal = identity.grant_principal
        
        # Check if it's a role
        if hasattr(principal, 'role_arn'):
            return principal.role_arn
        
        # Check if it's an assumed role principal (from sts)
        if hasattr(principal, 'assumed_role_arn'):
            return principal.assumed_role_arn
        
        # Check if it has an arn attribute
        if hasattr(principal, 'arn'):
            return principal.arn
        
        # If we can't extract the ARN, raise an error
        raise ValueError(
            f"Unable to extract principal ARN from {type(principal).__name__}. "
            f"The identity must have a 'role_arn', 'assumed_role_arn', or 'arn' attribute."
        )