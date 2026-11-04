"""Helpers shared by the construct tests, for reading a synthesized CloudFormation template"""
# Standard
import json
from typing import Optional, Tuple
# Installed
from aws_cdk.assertions import Template

# Account every test stack deploys to. A made-up one, so no test can reach AWS.
ACCOUNT = "123456789012"
# Hosted zone id that hosted_zone_context() hands to HostedZone.from_lookup
HOSTED_ZONE_ID = "Z0123456789ABCDEFGHIJ"


def _find_one(template: Template, resource_type: str, properties: Optional[dict]) -> Tuple[str, dict]:
    """Return the logical id and body of the one resource of `resource_type` whose properties include `properties`.

    Fails if there is none, or more than one, so a test cannot pass by reading the wrong resource.
    """
    pattern = None if properties is None else {"Properties": properties}
    found = template.find_resources(resource_type, pattern)
    assert len(found) == 1, f"expected one {resource_type} matching {properties}, found {list(found)}"
    return next(iter(found.items()))


def only_resource(template: Template, resource_type: str, properties: Optional[dict] = None) -> dict:
    """Return the one resource of `resource_type` whose properties include `properties` (see _find_one)."""
    return _find_one(template, resource_type, properties)[1]


def only_resource_id(template: Template, resource_type: str, properties: Optional[dict] = None) -> str:
    """Return the logical id of the one resource of `resource_type` whose properties include `properties`."""
    return _find_one(template, resource_type, properties)[0]


# The AWS managed policy CDK attaches to each Lambda role it creates, so the function can write its logs
LAMBDA_BASIC_EXECUTION_ROLE = {
    "Fn::Join": ["", ["arn:", {"Ref": "AWS::Partition"}, ":iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"]]
}


def role_policy_statements(template: Template, role_logical_id: str, aws_managed_policies: tuple = ()) -> list:
    """Return every statement that grants a role anything, gathered from all of its policies.

    That is the role's own inline Policies, every AWS::IAM::Policy and AWS::IAM::ManagedPolicy whose Roles include the
    role (alone or beside other roles), and every managed policy in the template that the role's ManagedPolicyArns
    refers to. A policy AWS manages is attached by ARN and its statements are not in the template, so the role's list
    of those must equal `aws_managed_policies` exactly; otherwise one could grant, say, AdministratorAccess unseen.
    """
    resources = template.to_json()["Resources"]
    role = resources[role_logical_id]["Properties"]
    attached = role.get("ManagedPolicyArns", [])
    in_template = [arn["Ref"] for arn in attached if isinstance(arn, dict) and arn.get("Ref") in resources]
    assert [arn for arn in attached if not (isinstance(arn, dict) and arn.get("Ref") in resources)] == list(
        aws_managed_policies
    ), attached
    policies = [policy["PolicyDocument"] for policy in role.get("Policies", [])]
    for logical_id, resource in resources.items():
        if resource["Type"] not in ("AWS::IAM::Policy", "AWS::IAM::ManagedPolicy"):
            continue
        if {"Ref": role_logical_id} in resource["Properties"].get("Roles", []) or logical_id in in_template:
            policies.append(resource["Properties"]["PolicyDocument"])
    return [statement for document in policies for statement in document["Statement"]]


def assert_same_statements(actual: list, expected: list) -> None:
    """Fail unless a policy holds exactly the expected statements: none missing, none extra, any order.

    Each statement is compared whole. Match.array_with and Match.object_like would only prove the expected
    statements are present, not that nothing broader sits beside them.
    """
    # Sorting the JSON text of each statement compares the two lists while ignoring their order
    assert sorted(json.dumps(s, sort_keys=True) for s in actual) == sorted(
        json.dumps(s, sort_keys=True) for s in expected
    )


def hosted_zone_context(domain_name: str, region: str) -> dict:
    """App context that answers HostedZone.from_lookup(domain_name=...) without calling AWS.

    The key and value have the form the CDK CLI writes to `cdk.context.json` after a real lookup, so synth
    uses HOSTED_ZONE_ID. Without it, synth falls back to the zone id "DUMMY", which tests would not notice.
    """
    key = f"hosted-zone:account={ACCOUNT}:domainName={domain_name}:region={region}"
    return {key: {"Id": f"/hostedzone/{HOSTED_ZONE_ID}", "Name": f"{domain_name}."}}
