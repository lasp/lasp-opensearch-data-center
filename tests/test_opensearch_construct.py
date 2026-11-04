"""Tests for the OpenSearch domain, snapshot role and snapshot Lambda that OpenSearchConstruct creates

The bounds of snapshot_retention_days, and the handler's pruning, are tested in test_snapshot_retention_cdk.py and
test_snapshot_handler.py.
"""
# Standard
import json
from pathlib import Path
from typing import Optional, Tuple, Union
# Installed
import aws_cdk as cdk
from aws_cdk import (
    aws_certificatemanager as acm,
    aws_events as events,
    aws_lambda as lambda_,
    aws_opensearchservice as opensearch,
    aws_route53 as route53,
    aws_s3 as s3,
)
from aws_cdk.assertions import Template
import pytest
# Local
from lasp_opensearch_data_center.constructs.opensearch import OpenSearchConstruct
from tests.construct_helpers import (
    ACCOUNT,
    HOSTED_ZONE_ID,
    assert_same_statements,
    only_resource,
    only_resource_id,
    LAMBDA_BASIC_EXECUTION_ROLE,
    role_policy_statements,
)

ENV = cdk.Environment(account=ACCOUNT, region="us-west-2")
CERTIFICATE_ARN = f"arn:aws:acm:us-west-2:{ACCOUNT}:certificate/00000000-0000-0000-0000-000000000000"


def _build(
        zone_name: str = "example.com", stack: Optional[cdk.Stack] = None, **kwargs
) -> Tuple[cdk.Stack, OpenSearchConstruct]:
    """Build the construct in a test stack (a new one unless `stack` is given), with a snapshot bucket, zone and
    certificate made for it, and any other arguments given.

    The zone and certificate are imported by attributes, so synth needs no AWS lookup.
    """
    if stack is None:
        stack = cdk.Stack(cdk.App(), "TestStack", env=ENV)
    zone = route53.HostedZone.from_hosted_zone_attributes(
        stack, "Zone", hosted_zone_id=HOSTED_ZONE_ID, zone_name=zone_name
    )
    certificate = acm.Certificate.from_certificate_arn(stack, "Certificate", CERTIFICATE_ARN)
    bucket = s3.Bucket(stack, "SnapshotBucket", bucket_name="test-snapshot")
    arguments = {
        "environment": ENV,
        "hosted_zone": zone,
        "certificate": certificate,
        "opensearch_snapshot_bucket": bucket,
        "opensearch_domain_name": "test-domain",
    }
    construct = OpenSearchConstruct(stack, "OpenSearch", **{**arguments, **kwargs})
    return stack, construct


def _template(**kwargs) -> Template:
    """Synthesize the construct built by _build(**kwargs)"""
    stack, _ = _build(**kwargs)
    return Template.from_stack(stack)


@pytest.fixture(scope="module")
def default_built() -> Tuple[cdk.Stack, OpenSearchConstruct, Template]:
    """The construct built with every optional argument left at its default"""
    stack, construct = _build()
    return stack, construct, Template.from_stack(stack)


def _domain(template: Template) -> dict:
    """Return the one OpenSearch domain"""
    return only_resource(template, "AWS::OpenSearchService::Domain")


def _snapshot_role_id(template: Template) -> str:
    """Return the logical id of the role OpenSearch assumes to write snapshots"""
    return only_resource_id(template, "AWS::IAM::Role", {"RoleName": "opensearch_snapshot_role"})


def _default_snapshot_lambda(template: Template) -> Tuple[str, dict]:
    """Return the logical id and body of the snapshot Lambda the construct builds (the one container-image function)"""
    function_id = only_resource_id(template, "AWS::Lambda::Function", {"PackageType": "Image"})
    return function_id, template.to_json()["Resources"][function_id]


def _text_of(value: Union[str, dict]) -> str:
    """Render a CloudFormation string as text, writing each reference in an Fn::Join as <Ref id> or <id.Attr>.

    The domain's access policy is a JSON string with the domain's ARN joined into it; rendering it as text lets a test
    parse it with json.loads and compare it whole.
    """
    if isinstance(value, str):
        return value
    separator, parts = value["Fn::Join"]
    rendered = []
    for part in parts:
        if isinstance(part, str):
            rendered.append(part)
        elif "Ref" in part:
            rendered.append(f"<Ref {part['Ref']}>")
        else:
            logical_id, attribute = part["Fn::GetAtt"]
            rendered.append(f"<{logical_id}.{attribute}>")
    return separator.join(rendered)


def _access_policy_statements(template: Template) -> list:
    """Return the statements of the domain access policy, which CDK applies through a custom resource"""
    resource = only_resource(template, "Custom::OpenSearchAccessPolicy")
    create_call = _text_of(resource["Properties"]["Create"])
    # An update must apply the same policy as the create
    assert _text_of(resource["Properties"]["Update"]) == create_call
    # The custom resource's call is JSON, and the policy inside it is a JSON string of its own
    access_policies = json.loads(create_call)["parameters"]["AccessPolicies"]
    return json.loads(access_policies)["Statement"]


# ---- Domain ----

def test_domain_defaults_to_one_t3_medium_data_node_and_manager_node_without_zone_awareness(default_built):
    """The defaults keep a first deployment cheap: one data node and one dedicated manager, both t3.medium."""
    _, _, template = default_built
    assert _domain(template)["Properties"]["ClusterConfig"] == {
        "DedicatedMasterCount": 1,
        "DedicatedMasterEnabled": True,
        "DedicatedMasterType": "t3.medium.search",
        "InstanceCount": 1,
        "InstanceType": "t3.medium.search",
        "ZoneAwarenessEnabled": False,
    }


def test_node_types_and_counts_reach_the_domain():
    """Each node argument lands on its own field; a swapped or dropped one would size the cluster wrongly."""
    template = _template(
        opensearch_data_node_instance_type="r7g.large.search",
        opensearch_data_node_count=3,
        opensearch_manager_node_instance_type="m7g.medium.search",
        opensearch_manager_node_count=5,
    )
    assert _domain(template)["Properties"]["ClusterConfig"] == {
        "DedicatedMasterCount": 5,
        "DedicatedMasterEnabled": True,
        "DedicatedMasterType": "m7g.medium.search",
        "InstanceCount": 3,
        "InstanceType": "r7g.large.search",
        "ZoneAwarenessEnabled": False,
    }


def test_zone_awareness_reaches_the_domain():
    """A zone awareness config spreads the data nodes across the given number of zones."""
    # 3 zones, since CDK fills in 2 when zone awareness is enabled without a count
    template = _template(
        opensearch_data_node_count=3,
        opensearch_zone_awareness=opensearch.ZoneAwarenessConfig(enabled=True, availability_zone_count=3),
    )
    cluster = _domain(template)["Properties"]["ClusterConfig"]
    assert cluster["ZoneAwarenessEnabled"] is True
    assert cluster["ZoneAwarenessConfig"] == {"AvailabilityZoneCount": 3}


def test_domain_name_and_version_default_to_opensearch_2_9(default_built):
    """The domain takes the given name, and runs OpenSearch 2.9 unless told otherwise."""
    _, _, template = default_built
    properties = _domain(template)["Properties"]
    assert properties["DomainName"] == "test-domain"
    assert properties["EngineVersion"] == "OpenSearch_2.9"


def test_opensearch_version_reaches_the_domain():
    """opensearch_version picks the engine the domain runs."""
    template = _template(opensearch_version=opensearch.EngineVersion.open_search("2.19"))
    assert _domain(template)["Properties"]["EngineVersion"] == "OpenSearch_2.19"


def test_volume_defaults_to_50_gb_of_gp3(default_built):
    """Each data node gets a 50 GB gp3 volume by default."""
    _, _, template = default_built
    assert _domain(template)["Properties"]["EBSOptions"] == {"EBSEnabled": True, "VolumeSize": 50, "VolumeType": "gp3"}


def test_volume_size_reaches_the_domain():
    """opensearch_volume_size sets each data node's volume, still gp3."""
    template = _template(opensearch_volume_size=300)
    assert _domain(template)["Properties"]["EBSOptions"] == {"EBSEnabled": True, "VolumeSize": 300, "VolumeType": "gp3"}


def test_domain_encrypts_at_rest_and_between_nodes(default_built):
    """Data is encrypted on disk and in transit between nodes."""
    _, _, template = default_built
    properties = _domain(template)["Properties"]
    assert properties["EncryptionAtRestOptions"] == {"Enabled": True}
    assert properties["NodeToNodeEncryptionOptions"] == {"Enabled": True}


def test_endpoint_is_https_only_on_search_dot_zone_with_tls_1_2_pfs(default_built):
    """Clients reach the domain only by HTTPS at search.<zone>, with the given certificate, and TLS 1.2+ PFS by default."""
    _, _, template = default_built
    assert _domain(template)["Properties"]["DomainEndpointOptions"] == {
        "CustomEndpoint": "search.example.com",
        "CustomEndpointCertificateArn": CERTIFICATE_ARN,
        "CustomEndpointEnabled": True,
        "EnforceHTTPS": True,
        "TLSSecurityPolicy": "Policy-Min-TLS-1-2-PFS-2023-10",
    }


def test_tls_security_policy_reaches_the_domain():
    """tls_security_policy replaces the TLS 1.2 PFS default on the endpoint."""
    template = _template(tls_security_policy=opensearch.TLSSecurityPolicy.TLS_1_2)
    assert _domain(template)["Properties"]["DomainEndpointOptions"]["TLSSecurityPolicy"] == "Policy-Min-TLS-1-2-2019-07"


def test_custom_endpoint_follows_the_hosted_zone():
    """The endpoint name and its CNAME record come from the zone passed in."""
    template = _template(zone_name="dev.data.example.org")
    assert _domain(template)["Properties"]["DomainEndpointOptions"]["CustomEndpoint"] == "search.dev.data.example.org"
    record = only_resource(template, "AWS::Route53::RecordSet")
    assert record["Properties"]["Name"] == "search.dev.data.example.org."
    assert record["Properties"]["HostedZoneId"] == HOSTED_ZONE_ID
    assert record["Properties"]["Type"] == "CNAME"


def test_domain_publishes_slow_search_slow_index_and_application_logs(default_built):
    """All three OpenSearch logs go to CloudWatch, and no others."""
    _, _, template = default_built
    logs = _domain(template)["Properties"]["LogPublishingOptions"]
    assert sorted(logs) == ["ES_APPLICATION_LOGS", "INDEX_SLOW_LOGS", "SEARCH_SLOW_LOGS"]
    for log in logs.values():
        assert log["Enabled"] is True


def test_domain_is_retained_by_default(default_built):
    """Deleting the stack keeps the domain and its data unless removal_policy says otherwise."""
    _, _, template = default_built
    domain = _domain(template)
    assert domain["DeletionPolicy"] == "Retain"
    assert domain["UpdateReplacePolicy"] == "Retain"


def test_removal_policy_reaches_the_domain():
    """removal_policy=DESTROY lets a stack teardown delete the domain."""
    domain = _domain(_template(removal_policy=cdk.RemovalPolicy.DESTROY))
    assert domain["DeletionPolicy"] == "Delete"
    assert domain["UpdateReplacePolicy"] == "Delete"


def test_domain_waits_for_the_opensearch_service_linked_role(default_built):
    """The domain cannot be created before OpenSearch's service-linked role exists, so it depends on it."""
    _, _, template = default_built
    role_id = only_resource_id(template, "AWS::IAM::ServiceLinkedRole")
    role = only_resource(template, "AWS::IAM::ServiceLinkedRole")
    assert role["Properties"]["AWSServiceName"] == "es.amazonaws.com"
    assert role_id in _domain(template)["DependsOn"]


# ---- Access policy ----

def test_access_policy_defaults_to_localhost_only(default_built):
    """By default nothing outside 127.0.0.1 may call the domain, so a deployment is closed until given a range."""
    _, _, template = default_built
    domain_id = only_resource_id(template, "AWS::OpenSearchService::Domain")
    assert _access_policy_statements(template) == [
        {
            "Action": "es:*",
            "Condition": {"IpAddress": {"aws:SourceIp": ["127.0.0.1/32"]}},
            "Effect": "Allow",
            "Principal": {"AWS": "*"},
            "Resource": f"<{domain_id}.Arn>/*",
        }
    ]
    # The domain carries no access policy of its own beside the one applied above
    assert "AccessPolicies" not in _domain(template)["Properties"]


def test_access_policy_allows_exactly_the_given_ranges():
    """Every CIDR passed in, and only those, may call the domain."""
    # Documentation ranges (RFC 5737), so the test cannot pass on a hard-coded real range
    ranges = ["192.0.2.0/24", "198.51.100.7/32"]
    template = _template(opensearch_ip_access_range=ranges)
    statements = _access_policy_statements(template)
    assert len(statements) == 1
    assert statements[0]["Condition"] == {"IpAddress": {"aws:SourceIp": ranges}}


# ---- Snapshot role ----

def test_snapshot_role_trusts_only_opensearch_for_this_domain_and_account():
    """Only this domain, in the account given by `environment`, may assume the role that writes snapshots."""
    # An account unlike the stack's, to show the condition comes from `environment`
    other_environment = cdk.Environment(account="210987654321", region="us-west-2")
    stack, _ = _build(environment=other_environment)
    template = Template.from_stack(stack)
    domain_id = only_resource_id(template, "AWS::OpenSearchService::Domain")
    role = only_resource(template, "AWS::IAM::Role", {"RoleName": "opensearch_snapshot_role"})
    assert role["Properties"]["AssumeRolePolicyDocument"]["Statement"] == [
        {
            "Action": "sts:AssumeRole",
            "Condition": {
                "StringEquals": {"aws:SourceAccount": "210987654321"},
                "ArnLike": {"aws:SourceArn": {"Fn::GetAtt": [domain_id, "Arn"]}},
            },
            "Effect": "Allow",
            "Principal": {"Service": "es.amazonaws.com"},
        }
    ]


def test_snapshot_role_may_only_list_read_write_and_delete_in_the_snapshot_bucket(default_built):
    """OpenSearch gets the S3 access a snapshot repository needs, on the snapshot bucket only."""
    stack, construct, template = default_built
    bucket_arn = stack.resolve(construct.opensearch_snapshot_bucket.bucket_arn)
    assert_same_statements(
        role_policy_statements(template, _snapshot_role_id(template)),
        [
            {"Action": "s3:ListBucket", "Effect": "Allow", "Resource": bucket_arn},
            {
                "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                "Effect": "Allow",
                "Resource": {"Fn::Join": ["", [bucket_arn, "/*"]]},
            },
        ],
    )


# ---- Snapshot Lambda ----

def test_default_snapshot_lambda_environment(default_built):
    """The snapshot Lambda is told the domain's https URL, the bucket, the role to pass, the repository name and how
    many days to keep snapshots."""
    _, _, template = default_built
    domain_id = only_resource_id(template, "AWS::OpenSearchService::Domain")
    _, function = _default_snapshot_lambda(template)
    assert function["Properties"]["Environment"]["Variables"] == {
        "OPEN_SEARCH_ENDPOINT": {"Fn::Join": ["", ["https://", {"Fn::GetAtt": [domain_id, "DomainEndpoint"]}, "/"]]},
        "SNAPSHOT_S3_BUCKET": {"Ref": only_resource_id(template, "AWS::S3::Bucket", {"BucketName": "test-snapshot"})},
        "SNAPSHOT_ROLE_ARN": {"Fn::GetAtt": [_snapshot_role_id(template), "Arn"]},
        "SNAPSHOT_REPO_NAME": "opensearch-snapshot-repo",
        "SNAPSHOT_RETENTION_DAYS": "90",
    }


def test_snapshot_repo_name_reaches_the_lambda():
    """snapshot_repo_name names the repository the Lambda registers and writes to."""
    _, function = _default_snapshot_lambda(_template(snapshot_repo_name="test-repo"))
    assert function["Properties"]["Environment"]["Variables"]["SNAPSHOT_REPO_NAME"] == "test-repo"


def test_default_snapshot_lambda_runs_15_minutes_with_512_mb_and_no_retries(default_built):
    """A snapshot call gets Lambda's longest timeout, and a failed run is not retried."""
    _, _, template = default_built
    function_id, function = _default_snapshot_lambda(template)
    assert function["Properties"]["Timeout"] == 15 * 60
    assert function["Properties"]["MemorySize"] == 512
    invoke_config = only_resource(template, "AWS::Lambda::EventInvokeConfig")
    assert invoke_config["Properties"] == {
        "FunctionName": {"Ref": function_id},
        "MaximumRetryAttempts": 0,
        "Qualifier": "$LATEST",
    }


def test_default_snapshot_lambda_image_is_the_snapshot_lambda_stage_for_amd64(default_built):
    """The image is built from the packaged lambda/Dockerfile's snapshot-lambda stage, for x86_64 Lambda."""
    stack, _, _ = default_built
    assembly = stack.node.root.synth()
    asset_manifest = json.loads(Path(assembly.directory, f"{stack.artifact_id}.assets.json").read_text())
    images = list(asset_manifest["dockerImages"].values())
    assert len(images) == 1
    source = images[0]["source"]
    assert source["dockerBuildTarget"] == "snapshot-lambda"
    assert source["platform"] == "linux/amd64"
    # The staged build context must define that stage, or the image build fails at deploy time
    dockerfile = Path(assembly.directory, source["directory"], "Dockerfile").read_text()
    assert "AS snapshot-lambda" in dockerfile


def test_default_snapshot_lambda_may_call_the_domain_and_pass_only_the_snapshot_role(default_built):
    """The Lambda may call the domain's HTTP API and hand OpenSearch the snapshot role, and nothing else."""
    _, _, template = default_built
    domain_id = only_resource_id(template, "AWS::OpenSearchService::Domain")
    function_id, function = _default_snapshot_lambda(template)
    lambda_role_id = function["Properties"]["Role"]["Fn::GetAtt"][0]
    assert_same_statements(
        role_policy_statements(template, lambda_role_id, (LAMBDA_BASIC_EXECUTION_ROLE,)),
        [
            {
                "Action": "es:*",
                "Effect": "Allow",
                "Resource": {"Fn::Join": ["", [{"Fn::GetAtt": [domain_id, "Arn"]}, "/*"]]},
            },
            {
                "Action": "iam:PassRole",
                "Effect": "Allow",
                "Resource": {"Fn::GetAtt": [_snapshot_role_id(template), "Arn"]},
            },
        ],
    )


def test_retention_is_not_checked_for_a_lambda_passed_in():
    """snapshot_retention_days has no effect on a Lambda passed in, so a value the default Lambda would refuse is
    accepted."""
    stack = cdk.Stack(cdk.App(), "TestStack", env=ENV)
    given = lambda_.Function(
        stack,
        "GivenSnapshotLambda",
        runtime=lambda_.Runtime.PYTHON_3_13,
        handler="index.handler",
        code=lambda_.Code.from_inline("def handler(event, context): pass"),
    )
    _build(stack=stack, snapshot_lambda=given, snapshot_retention_days=0)
    Template.from_stack(stack)


def test_snapshot_lambda_argument_replaces_the_default():
    """A Lambda passed in is scheduled and granted access, and the construct builds no image Lambda of its own."""
    stack = cdk.Stack(cdk.App(), "TestStack", env=ENV)
    given = lambda_.Function(
        stack,
        "GivenSnapshotLambda",
        runtime=lambda_.Runtime.PYTHON_3_13,
        handler="index.handler",
        code=lambda_.Code.from_inline("def handler(event, context): pass"),
    )
    _build(stack=stack, snapshot_lambda=given)
    template = Template.from_stack(stack)
    template.resource_properties_count_is("AWS::Lambda::Function", {"PackageType": "Image"}, 0)
    given_id = stack.get_logical_id(given.node.default_child)
    rule = only_resource(template, "AWS::Events::Rule")
    assert rule["Properties"]["Targets"] == [{"Arn": {"Fn::GetAtt": [given_id, "Arn"]}, "Id": "Target0"}]
    domain_id = only_resource_id(template, "AWS::OpenSearchService::Domain")
    given_role_id = stack.get_logical_id(given.role.node.default_child)
    assert_same_statements(
        role_policy_statements(template, given_role_id, (LAMBDA_BASIC_EXECUTION_ROLE,)),
        [
            {
                "Action": "es:*",
                "Effect": "Allow",
                "Resource": {"Fn::Join": ["", [{"Fn::GetAtt": [domain_id, "Arn"]}, "/*"]]},
            },
            {
                "Action": "iam:PassRole",
                "Effect": "Allow",
                "Resource": {"Fn::GetAtt": [_snapshot_role_id(template), "Arn"]},
            },
        ],
    )


def test_schedule_defaults_to_daily_at_9_utc(default_built):
    """One rule runs the snapshot Lambda every day at 09:00 UTC."""
    _, _, template = default_built
    function_id, _ = _default_snapshot_lambda(template)
    rule = only_resource(template, "AWS::Events::Rule")
    assert rule["Properties"] == {
        "Description": "Scheduler to trigger the OpenSearch Lambda snapshot function",
        "Name": "SnapshotLambdaScheduler",
        "ScheduleExpression": "cron(0 9 ? * * *)",
        "State": "ENABLED",
        "Targets": [{"Arn": {"Fn::GetAtt": [function_id, "Arn"]}, "Id": "Target0"}],
    }


def test_snapshot_schedule_reaches_the_rule():
    """snapshot_schedule replaces the daily 09:00 UTC schedule."""
    template = _template(snapshot_schedule=events.Schedule.rate(cdk.Duration.hours(6)))
    assert only_resource(template, "AWS::Events::Rule")["Properties"]["ScheduleExpression"] == "rate(6 hours)"
