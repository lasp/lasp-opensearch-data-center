from aws_cdk import (
    aws_s3 as s3,
    aws_cloudfront as cloudfront,
    aws_cloudfront_origins as origins,
    aws_certificatemanager as acm,
    aws_iam,
    aws_route53 as route53,
    aws_route53_targets as targets,
    aws_wafv2 as wafv2,
    Duration,
    Environment,
)
from constructs import Construct


class FrontEndConstruct(Construct):
    """
    Create a *self-contained* construct to create the resources required for the Frontend
    Web team to deploy their JS app and serve it over https within AWS.

    This construct must be deployed in us-east-1 for the WAF IP restrictions!

    It takes ~5-8 minutes to redeploy this construct for any Cloudfront updates.

    Note: In order to provide https access to the S3 content, Cloudfront is
    required to provide the SSL termination from the browser request.
    Cloudfront also provides extra layers of caching and a larger amount
    of free egress each month (1TB as of 10/2022).
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        account_type: str,
        domain_name: str,
        frontend_bucket: s3.Bucket,
        waf_ip_range: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Import hosted zone which was created manually during dev and prod account setup
        # during domain registration
        self.hosted_zone = route53.HostedZone.from_lookup(
            self, "ItdcHostedZone", domain_name=domain_name
        )

        # This sets the website to whatever the passed in domain name is.
        website_url = self.hosted_zone.zone_name

        # Define CI/CD group and policy names
        frontend_group_name = f"{account_type}FrontEndGroup"
        frontend_policy_name = f"{account_type}-frontend-automated-deploy"

        # Create a frontend group, IAM users will be added to this group
        # via the AWS CLI
        self.frontend_deployment_group = aws_iam.Group(
            self, frontend_group_name, group_name=frontend_group_name
        )

        # Create the IAM policy to allow S3 access and attach it to the group
        self.frontend_iam_policy = aws_iam.ManagedPolicy(
            self,
            frontend_policy_name,
            description="CI CD automated frontend policy",
            managed_policy_name=frontend_policy_name,
            groups=[
                self.frontend_deployment_group,
            ],
            statements=[
                aws_iam.PolicyStatement(
                    # Permission to list all S3 buckets
                    effect=aws_iam.Effect.ALLOW,
                    actions=["s3:GetBucketLocation", "s3:ListAllMyBuckets"],
                    resources=[
                        "arn:aws:s3:::*",
                    ],
                ),
                aws_iam.PolicyStatement(
                    effect=aws_iam.Effect.ALLOW,
                    actions=[
                        "s3:*",
                    ],
                    resources=[
                        # All bucket objects must start with the keyword "frontend"
                        # Additional restrictions imposed at the resource/bucket level
                        frontend_bucket.bucket_arn,
                        frontend_bucket.bucket_arn + "/frontend/*",
                    ],
                ),
            ],
        )

        # Create the specific CF cert
        self.cloudfront_cert = acm.Certificate(
            self,
            "cloudfront_cert",
            domain_name=website_url,
            validation=acm.CertificateValidation.from_dns(hosted_zone=self.hosted_zone),
        )

        # Create an IP Set with the specified IP address range
        self.cfn_iPSet = wafv2.CfnIPSet(
            self,
            "ACLIPSet",
            addresses=[waf_ip_range],
            ip_address_version="IPV4",
            scope="CLOUDFRONT",
            description="Web ACL IP Range",
            name="WebACLIPRange",
        )

        # Create a WAF to enforce IP restrictions to CloudFront
        wafacl = wafv2.CfnWebACL(
            self,
            id="WAF",
            custom_response_bodies={
                "Custom401ErrorMessage": wafv2.CfnWebACL.CustomResponseBodyProperty(
                    content="Error: You are not on a Network/VPN with access to this webpage.",
                    content_type="TEXT_PLAIN",
                ),
            },
            # Block all access that doesn't match any rule
            default_action=wafv2.CfnWebACL.DefaultActionProperty(
                block=wafv2.CfnWebACL.BlockActionProperty(
                    custom_response=wafv2.CfnWebACL.CustomResponseProperty(
                        response_code=401,
                        custom_response_body_key="Custom401ErrorMessage",
                    ),
                ),
            ),
            ## The scope of this Web ACL.
            ## Valid options: CLOUDFRONT, REGIONAL.
            ## For CLOUDFRONT, you must create your WAFv2 resources
            ## in the US East (N. Virginia) Region, us-east-1
            ## https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-wafv2-webacl.html#cfn-wafv2-webacl-scope
            scope="CLOUDFRONT",
            ## Defines and enables Amazon CloudWatch metrics and web request sample collection.
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name="WAF-CloudFront",
                sampled_requests_enabled=True,
            ),
            description="WAFv2 ACL for CloudFront",
            name="waf-cloudfront",
            rules=[
                # Rule to allow specified ACL network IPs only
                wafv2.CfnWebACL.RuleProperty(
                    name="ACLIPsOnly",
                    priority=0,
                    statement=wafv2.CfnWebACL.StatementProperty(
                        ip_set_reference_statement=wafv2.CfnWebACL.IPSetReferenceStatementProperty(
                            # This function only takes in an ARN
                            arn=self.cfn_iPSet.attr_arn,
                        ),
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True,
                        metric_name="WafRuleACLIPsOnly",
                        sampled_requests_enabled=True,
                    ),
                    action=wafv2.CfnWebACL.RuleActionProperty(allow={}),
                )
            ],
        )

        # Create a new CF dist to serve the frontend App over https
        self.distribution = cloudfront.Distribution(
            self,
            "cloudfront_distribution",
            default_behavior=cloudfront.BehaviorOptions(
                # origin_path restricts CloudFront access to frontend/live S3 data
                origin=origins.S3Origin(
                    frontend_bucket,
                    origin_path="frontend/live/",
                ),
                viewer_protocol_policy=cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
                # TODO: decide if want to enable/disable caching for S3 bucket updates
                cache_policy=cloudfront.CachePolicy.CACHING_DISABLED,
            ),
            # CF will be accessed via the web_url set at the top of this script
            domain_names=[website_url],
            # Lowest price limits to US/CANADA/EU
            price_class=cloudfront.PriceClass.PRICE_CLASS_100,
            certificate=self.cloudfront_cert,
            default_root_object="/index.html",
            comment="CF dist to serve IT S3 frontend content",
            error_responses=[
                # Web Team frontend apps uses SPA, so redirects are required
                # Only 403 redirects needed, 404 never encountered due to bucket policy
                cloudfront.ErrorResponse(
                    http_status=403,
                    response_http_status=200,
                    response_page_path="/index.html",
                    ttl=Duration.seconds(10),
                ),
            ],
            web_acl_id=wafacl.attr_arn,
        )

        # DNS record for CF -> S3 bucket access
        # May need to wait 5-10 minutes after this record is created
        # to propogate through all domain servers
        self.route53_domain_name = route53.ARecord(
            self,
            "CFAliasRecord",
            zone=self.hosted_zone,
            record_name=website_url,
            target=route53.RecordTarget.from_alias(
                targets.CloudFrontTarget(self.distribution)
            ),
        )
