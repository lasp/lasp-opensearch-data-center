"""Storage resources for the front end web application for a data center"""
from aws_cdk import (
    aws_s3 as s3,
    aws_route53 as route53,
    aws_iam,
    Environment,
    RemovalPolicy,
)
from constructs import Construct


class FrontendStorageConstruct(Construct):
    """
    Construct that creates an S3 bucket used by the Frontend
    stack for site content.

    This stack must be deployed in us-east-1 for the WAF IP restrictions!

    Note: In order to provide https access to the S3 content, Cloudfront is
    required to provide the SSL termination from the browser request.
    Cloudfront also provides extra layers of caching and a larger amount
    of free egress each month (1TB as of 10/2022).
    """

    def __init__(
            self,
            scope: Construct,
            construct_id: str,
            *,
            environment: Environment,
            domain_name: str
    ) -> None:
        """Construct init

        :param scope: Construct
            The scope in which this Construct is instantiated, usually the `self` inside a Stack.
        :param construct_id: str
            ID for this construct instance, e.g. "MyFrontendStorageConstruct"
        :param environment: Environment
            AWS environment (account and region)
        :param domain_name: str
        """
        super().__init__(scope, construct_id)

        if environment.region != "us-east-1":
            raise ValueError(
                "The front end stack MUST be deployed to us-east-1 for cloudfront WAF IP reasons."
            )

        # Import existing Hosted Zone for the registered domain name.
        # This HZ must exist external to the Construct (e.g. in the owner Stack)
        self.hosted_zone = route53.HostedZone.from_lookup(
            self, "FrontEndHostedZone", domain_name=domain_name
        )

        # This sets the website to whatever the passed in domain name is. That should be
        # dev.my-domain.com, prod.my-domain.com, or a developer's own dev domain
        website_url = self.hosted_zone.zone_name

        # Create a bucket that a web development team
        # can programmatically PUT data into
        self.frontend_bucket = s3.Bucket(
            self,
            "FrontendS3bucket",
            bucket_name=website_url,
            # Disable object ACL & use bucket resource policy for permissions
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            # This will remove a bucket via the CDK if there are no objects within it
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Restrict puts/deletes to the "frontend/" folder to a specific tag
        # An IAM user must be generated for the webteam via the AWS CLI
        # and tagged with group:frontend
        #
        # For the DEV account the Web team will put their built JS apps into this object path:
        # "frontend/libera/live/"
        # For the PROD account the Web team will put their built JS apps into this versioned object path:
        # "frontend/libera/VXX/"
        #
        # All webteam PUTs will be performed via Jenkins using CLI credentials
        self.frontend_bucket.add_to_resource_policy(
            aws_iam.PolicyStatement(
                effect=aws_iam.Effect.DENY,
                principals=[aws_iam.AnyPrincipal()],
                actions=[
                    "s3:DeleteObject",
                    "s3:PutObject",
                ],
                resources=[
                    self.frontend_bucket.bucket_arn,
                    self.frontend_bucket.arn_for_objects("frontend/*"),
                ],
                conditions={"StringNotLike": {"aws:PrincipalTag/group": "*frontend*"}},
            )
        ),
