"""Tests for FrontendConstruct's website resources: guest access, deploy role, certificate, WAF and CloudFront"""
# Standard
from dataclasses import dataclass
# Installed
import aws_cdk as cdk
from aws_cdk import aws_cloudfront as cloudfront, aws_s3 as s3
from aws_cdk.assertions import Template
import pytest
# Local
from lasp_opensearch_data_center.constructs.frontend import FrontendConstruct
from tests.construct_helpers import (
    ACCOUNT,
    HOSTED_ZONE_ID,
    assert_same_statements,
    hosted_zone_context,
    only_resource,
    only_resource_id,
    role_policy_statements,
)

REGION = "us-east-1"
# A documentation range (RFC 5737), so the test cannot pass on a hard-coded real range
WAF_IP_RANGE = "192.0.2.0/24"


@dataclass
class Built:
    """A synthesized test stack and the site bucket the test passed to the construct"""
    stack: cdk.Stack
    template: Template
    frontend_bucket: s3.Bucket

    def bucket_arn(self) -> dict:
        """The CloudFormation expression for the site bucket's ARN"""
        return {"Fn::GetAtt": [self.stack.get_logical_id(self.frontend_bucket.node.default_child), "Arn"]}


def _build(domain_name: str = "example.com", region: str = REGION, waf_ip_range: str = WAF_IP_RANGE) -> Built:
    """Synthesize the construct with a site bucket, answering its hosted zone lookup from context instead of AWS"""
    environment = cdk.Environment(account=ACCOUNT, region=region)
    app = cdk.App(context=hosted_zone_context(domain_name, region))
    stack = cdk.Stack(app, "TestStack", env=environment)
    bucket = s3.Bucket(stack, "SiteBucket", bucket_name="test-site")
    FrontendConstruct(
        stack,
        "Frontend",
        environment=environment,
        account_type="test",
        domain_name=domain_name,
        frontend_bucket=bucket,
        waf_ip_range=waf_ip_range,
    )
    return Built(stack=stack, template=Template.from_stack(stack), frontend_bucket=bucket)


@pytest.fixture(scope="module")
def built() -> Built:
    """The construct built with the default test arguments"""
    return _build()


def _distribution_config(template: Template) -> dict:
    """Return the DistributionConfig of the one CloudFront distribution"""
    return only_resource(template, "AWS::CloudFront::Distribution")["Properties"]["DistributionConfig"]


def test_refuses_any_region_but_us_east_1():
    """CloudFront's WAF and certificate must live in us-east-1, so the construct refuses to be built anywhere else."""
    with pytest.raises(ValueError, match="us-east-1"):
        _build(region="us-west-2")


# ---- Guest access for the website ----

def test_identity_pool_allows_unauthenticated_identities(built):
    """Website visitors do not sign in, so the identity pool hands out guest identities."""
    pool = only_resource(built.template, "AWS::Cognito::IdentityPool")
    assert pool["Properties"] == {"AllowUnauthenticatedIdentities": True, "IdentityPoolName": "Website Identity Pool"}


def test_guest_role_trusts_only_unauthenticated_identities_from_this_pool(built):
    """Only a guest identity issued by this site's pool can assume the guest role."""
    pool_id = only_resource_id(built.template, "AWS::Cognito::IdentityPool")
    role = only_resource(built.template, "AWS::IAM::Role", {"RoleName": "website-guest-role"})
    assert role["Properties"]["AssumeRolePolicyDocument"]["Statement"] == [
        {
            "Action": "sts:AssumeRoleWithWebIdentity",
            "Condition": {
                "StringEquals": {"cognito-identity.amazonaws.com:aud": {"Ref": pool_id}},
                "ForAnyValue:StringLike": {"cognito-identity.amazonaws.com:amr": "unauthenticated"},
            },
            "Effect": "Allow",
            "Principal": {"Federated": "cognito-identity.amazonaws.com"},
        }
    ]


def test_guest_role_may_only_get_credentials_and_metric_images(built):
    """Guests may fetch CloudWatch metric images for the site's plots, and nothing else."""
    role_id = only_resource_id(built.template, "AWS::IAM::Role", {"RoleName": "website-guest-role"})
    assert_same_statements(
        role_policy_statements(built.template, role_id),
        [
            {
                "Action": ["cognito-identity:GetCredentialsForIdentity", "cloudwatch:GetMetricWidgetImage"],
                "Effect": "Allow",
                # GetMetricWidgetImage supports no narrower resource than "*"
                "Resource": "*",
            }
        ],
    )


def test_identity_pool_gives_guests_the_guest_role(built):
    """The pool's unauthenticated identities get the guest role, and no role is set for signed-in ones."""
    pool_id = only_resource_id(built.template, "AWS::Cognito::IdentityPool")
    role_id = only_resource_id(built.template, "AWS::IAM::Role", {"RoleName": "website-guest-role"})
    attachment = only_resource(built.template, "AWS::Cognito::IdentityPoolRoleAttachment")
    assert attachment["Properties"] == {
        "IdentityPoolId": {"Ref": pool_id},
        "Roles": {"unauthenticated": {"Fn::GetAtt": [role_id, "Arn"]}},
    }


# ---- Deploy user and role ----

def test_deploy_role_trusts_only_the_deploy_user(built):
    """The frontend team's CI user, and no one else, may assume the deploy role."""
    user_id = only_resource_id(built.template, "AWS::IAM::User", {"UserName": "frontend-deploy-user"})
    role = only_resource(built.template, "AWS::IAM::Role", {"RoleName": "frontend-deploy-role"})
    assert role["Properties"]["AssumeRolePolicyDocument"]["Statement"] == [
        {"Action": "sts:AssumeRole", "Effect": "Allow", "Principal": {"AWS": {"Fn::GetAtt": [user_id, "Arn"]}}}
    ]


def test_deploy_user_has_no_permissions_of_its_own(built):
    """The deploy user can only assume the deploy role; it holds no policy itself."""
    user = only_resource(built.template, "AWS::IAM::User", {"UserName": "frontend-deploy-user"})
    assert user["Properties"] == {"UserName": "frontend-deploy-user"}
    user_id = only_resource_id(built.template, "AWS::IAM::User", {"UserName": "frontend-deploy-user"})
    built.template.resource_properties_count_is("AWS::IAM::Policy", {"Users": [{"Ref": user_id}]}, 0)


def test_deploy_policy_reaches_only_the_site_bucket_and_its_frontend_prefix(built):
    """The deploy role may list the site bucket and upload files under frontend/, nothing more."""
    role_id = only_resource_id(built.template, "AWS::IAM::Role", {"RoleName": "frontend-deploy-role"})
    assert_same_statements(
        role_policy_statements(built.template, role_id),
        [
            {"Action": "s3:ListBucket", "Effect": "Allow", "Resource": built.bucket_arn()},
            {
                "Action": ["s3:PutObject", "s3:AbortMultipartUpload"],
                "Effect": "Allow",
                "Resource": {"Fn::Join": ["", [built.bucket_arn(), "/frontend/*"]]},
            },
        ],
    )


# ---- Certificate, WAF and CloudFront ----

def test_certificate_covers_the_site_domain_and_is_validated_through_its_zone(built):
    """CloudFront serves the site at the bare domain, so the certificate names exactly that domain."""
    certificate = only_resource(built.template, "AWS::CertificateManager::Certificate")
    assert certificate["Properties"]["DomainName"] == "example.com"
    assert certificate["Properties"]["ValidationMethod"] == "DNS"
    assert certificate["Properties"]["DomainValidationOptions"] == [
        {"DomainName": "example.com", "HostedZoneId": HOSTED_ZONE_ID}
    ]
    assert "SubjectAlternativeNames" not in certificate["Properties"]


def test_domain_name_reaches_the_certificate_distribution_and_record():
    """Another domain changes every place the site's name appears."""
    built = _build(domain_name="dev.data.example.org")
    certificate = only_resource(built.template, "AWS::CertificateManager::Certificate")
    assert certificate["Properties"]["DomainName"] == "dev.data.example.org"
    assert _distribution_config(built.template)["Aliases"] == ["dev.data.example.org"]
    record = only_resource(built.template, "AWS::Route53::RecordSet")
    assert record["Properties"]["Name"] == "dev.data.example.org."
    assert record["Properties"]["HostedZoneId"] == HOSTED_ZONE_ID


def test_waf_ip_set_holds_the_given_range(built):
    """The IP set the WAF allows holds exactly the range passed in."""
    ip_set = only_resource(built.template, "AWS::WAFv2::IPSet")
    assert ip_set["Properties"] == {
        "Addresses": [WAF_IP_RANGE],
        "Description": "Web ACL IP Range",
        "IPAddressVersion": "IPV4",
        "Name": "WebACLIPRange",
        "Scope": "CLOUDFRONT",
    }


def test_waf_ip_range_argument_reaches_the_ip_set():
    """A different waf_ip_range replaces the address in the IP set."""
    built = _build(waf_ip_range="198.51.100.0/24")
    ip_set = only_resource(built.template, "AWS::WAFv2::IPSet")
    assert ip_set["Properties"]["Addresses"] == ["198.51.100.0/24"]


def test_waf_allows_the_ip_set_and_blocks_everyone_else_with_a_401(built):
    """Requests from outside the IP set get a 401 with a message about the network or VPN."""
    ip_set_id = only_resource_id(built.template, "AWS::WAFv2::IPSet")
    acl = only_resource(built.template, "AWS::WAFv2::WebACL")
    properties = acl["Properties"]
    assert properties["Scope"] == "CLOUDFRONT"
    assert properties["DefaultAction"] == {
        "Block": {"CustomResponse": {"CustomResponseBodyKey": "Custom401ErrorMessage", "ResponseCode": 401}}
    }
    assert properties["CustomResponseBodies"] == {
        "Custom401ErrorMessage": {
            "Content": "Error: You are not on a Network/VPN with access to this webpage.",
            "ContentType": "TEXT_PLAIN",
        }
    }
    assert properties["Rules"] == [
        {
            "Action": {"Allow": {}},
            "Name": "ACLIPsOnly",
            "Priority": 0,
            "Statement": {"IPSetReferenceStatement": {"Arn": {"Fn::GetAtt": [ip_set_id, "Arn"]}}},
            "VisibilityConfig": {
                "CloudWatchMetricsEnabled": True,
                "MetricName": "WafRuleACLIPsOnly",
                "SampledRequestsEnabled": True,
            },
        }
    ]


def test_distribution_sits_behind_the_waf_and_serves_https_with_the_certificate(built):
    """CloudFront applies the WAF, answers at the site domain with its certificate, and redirects HTTP to HTTPS."""
    acl_id = only_resource_id(built.template, "AWS::WAFv2::WebACL")
    certificate_id = only_resource_id(built.template, "AWS::CertificateManager::Certificate")
    config = _distribution_config(built.template)
    assert config["WebACLId"] == {"Fn::GetAtt": [acl_id, "Arn"]}
    assert config["Aliases"] == ["example.com"]
    assert config["ViewerCertificate"] == {
        "AcmCertificateArn": {"Ref": certificate_id},
        "MinimumProtocolVersion": "TLSv1.2_2021",
        "SslSupportMethod": "sni-only",
    }
    assert config["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
    assert config["PriceClass"] == "PriceClass_100"


def test_distribution_serves_frontend_live_uncached_through_the_origin_access_identity(built):
    """CloudFront reads only frontend/live/ from the bucket, through its own identity, and does not cache it."""
    identity_id = only_resource_id(built.template, "AWS::CloudFront::CloudFrontOriginAccessIdentity")
    config = _distribution_config(built.template)
    assert len(config["Origins"]) == 1
    origin = config["Origins"][0]
    bucket_id = built.stack.get_logical_id(built.frontend_bucket.node.default_child)
    assert origin["DomainName"] == {"Fn::GetAtt": [bucket_id, "RegionalDomainName"]}
    # CDK writes the construct's "frontend/live/" in the form CloudFront requires: a leading slash and no trailing one
    assert origin["OriginPath"] == "/frontend/live"
    assert origin["S3OriginConfig"] == {
        "OriginAccessIdentity": {"Fn::Join": ["", ["origin-access-identity/cloudfront/", {"Ref": identity_id}]]}
    }
    assert config["DefaultCacheBehavior"]["CachePolicyId"] == cloudfront.CachePolicy.CACHING_DISABLED.cache_policy_id


def test_distribution_answers_403_with_the_app_page(built):
    """The site is a single-page app, so a 403 for an unknown path returns /index.html with a 200, cached 10 seconds."""
    assert _distribution_config(built.template)["CustomErrorResponses"] == [
        {"ErrorCachingMinTTL": 10, "ErrorCode": 403, "ResponseCode": 200, "ResponsePagePath": "/index.html"}
    ]


def test_distribution_serves_index_html_at_the_root(built):
    """A request for the site's root returns index.html; AWS says the name must not start with "/"."""
    assert _distribution_config(built.template)["DefaultRootObject"] == "index.html"


def test_bucket_policy_lets_only_cloudfront_read_objects(built):
    """The construct adds one statement to the site bucket's policy: the origin access identity may get objects."""
    identity_id = only_resource_id(built.template, "AWS::CloudFront::CloudFrontOriginAccessIdentity")
    policy = only_resource(built.template, "AWS::S3::BucketPolicy")
    assert_same_statements(
        policy["Properties"]["PolicyDocument"]["Statement"],
        [
            {
                "Action": "s3:GetObject",
                "Effect": "Allow",
                "Principal": {"CanonicalUser": {"Fn::GetAtt": [identity_id, "S3CanonicalUserId"]}},
                "Resource": {"Fn::Join": ["", [built.bucket_arn(), "/*"]]},
            }
        ],
    )


def test_alias_record_points_the_site_domain_at_the_distribution(built):
    """An A record in the site's zone sends the domain to CloudFront."""
    distribution_id = only_resource_id(built.template, "AWS::CloudFront::Distribution")
    record = only_resource(built.template, "AWS::Route53::RecordSet")
    assert record["Properties"]["Type"] == "A"
    assert record["Properties"]["Name"] == "example.com."
    assert record["Properties"]["HostedZoneId"] == HOSTED_ZONE_ID
    assert record["Properties"]["AliasTarget"]["DNSName"] == {"Fn::GetAtt": [distribution_id, "DomainName"]}
