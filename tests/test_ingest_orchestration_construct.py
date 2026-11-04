"""Tests for the ingest status table, Lambda wiring and backups that IngestProcessingConstruct sets up"""
# Standard
from dataclasses import dataclass
from typing import Optional
# Installed
import aws_cdk as cdk
from aws_cdk import (
    aws_backup as backup,
    aws_lambda as lambda_,
    aws_opensearchservice as opensearch,
    aws_s3 as s3,
    aws_sqs as sqs,
)
from aws_cdk.assertions import Template
import pytest
# Local
from lasp_opensearch_data_center.constructs.constants import DropboxLambdaEnv, IngestLambdaEnv
from lasp_opensearch_data_center.constructs.ingest_orchestration import IngestProcessingConstruct
from tests.construct_helpers import (
    ACCOUNT,
    assert_same_statements,
    only_resource,
    only_resource_id,
    LAMBDA_BASIC_EXECUTION_ROLE,
    role_policy_statements,
)

ENV = cdk.Environment(account=ACCOUNT, region="us-west-2")
DOMAIN_ARN = f"arn:aws:es:us-west-2:{ACCOUNT}:domain/test-domain"
DOMAIN_ENDPOINT = "search-test-domain-abc123.us-west-2.es.amazonaws.com"


@dataclass
class Built:
    """A synthesized test stack and the resources the test made to pass to the construct"""
    stack: cdk.Stack
    template: Template
    dropbox_bucket: s3.Bucket
    ingest_bucket: s3.Bucket
    dropbox_queue: sqs.Queue
    ingest_queue: sqs.Queue
    dropbox_lambda: lambda_.Function
    ingest_lambda: lambda_.Function
    backup_vault: Optional[backup.BackupVault]

    def id_of(self, resource) -> str:
        """Logical id of a resource the test made, e.g. built.id_of(built.ingest_queue)"""
        return self.stack.get_logical_id(resource.node.default_child)

    def lambda_environment(self, function: lambda_.Function) -> dict:
        """The environment variables a Lambda ends up with"""
        body = self.template.to_json()["Resources"][self.id_of(function)]
        return body["Properties"]["Environment"]["Variables"]

    def lambda_role_statements(self, function: lambda_.Function) -> list:
        """Every statement that grants a Lambda's role anything, beside the log-writing policy CDK always attaches"""
        return role_policy_statements(self.template, self.id_of(function.role), (LAMBDA_BASIC_EXECUTION_ROLE,))


def _function(stack: cdk.Stack, construct_id: str) -> lambda_.Function:
    """A placeholder Lambda: the construct only adds environment, permissions and triggers to the ones it is given"""
    return lambda_.Function(
        stack,
        construct_id,
        runtime=lambda_.Runtime.PYTHON_3_13,
        handler="index.handler",
        code=lambda_.Code.from_inline("def handler(event, context): pass"),
    )


def _build(with_backup_vault: bool = False, **kwargs) -> Built:
    """Synthesize the construct with test buckets, queues, Lambdas and an imported domain, plus any arguments given.

    With with_backup_vault=True it also makes a backup vault and passes it as backup_vault.
    """
    stack = cdk.Stack(cdk.App(), "TestStack", env=ENV)
    built_parts = {
        "dropbox_bucket": s3.Bucket(stack, "DropboxBucket", bucket_name="test-dropbox"),
        "ingest_bucket": s3.Bucket(stack, "IngestBucket", bucket_name="test-ingest"),
        "dropbox_queue": sqs.Queue(stack, "DropboxQueue"),
        "ingest_queue": sqs.Queue(stack, "IngestQueue"),
        "dropbox_lambda": _function(stack, "DropboxLambda"),
        "ingest_lambda": _function(stack, "IngestLambda"),
    }
    # Imported by attributes, so synth needs no real domain
    domain = opensearch.Domain.from_domain_attributes(
        stack, "Domain", domain_arn=DOMAIN_ARN, domain_endpoint=DOMAIN_ENDPOINT
    )
    backup_vault = None
    if with_backup_vault:
        backup_vault = backup.BackupVault(stack, "Vault", backup_vault_name="test-vault")
        kwargs["backup_vault"] = backup_vault
    IngestProcessingConstruct(stack, "Ingest", open_search_domain=domain, **built_parts, **kwargs)
    return Built(stack=stack, template=Template.from_stack(stack), backup_vault=backup_vault, **built_parts)


@pytest.fixture(scope="module")
def default_built() -> Built:
    """The construct built with every optional argument left at its default"""
    return _build()


def _table_id(built: Built) -> str:
    """Logical id of the ingest status table"""
    return only_resource_id(built.template, "AWS::DynamoDB::GlobalTable", {"TableName": "ingest_status"})


# ---- Ingest status table ----

def test_ingest_status_table_keys_and_file_name_index(default_built):
    """The ingest code queries by file type and start time, and looks up file names through a keys-only index."""
    table = only_resource(default_built.template, "AWS::DynamoDB::GlobalTable")
    properties = table["Properties"]
    assert properties["TableName"] == "ingest_status"
    assert properties["KeySchema"] == [
        {"AttributeName": "file_type", "KeyType": "HASH"},
        {"AttributeName": "ingest_started", "KeyType": "RANGE"},
    ]
    assert properties["GlobalSecondaryIndexes"] == [
        {
            "IndexName": "file_name_index",
            "KeySchema": [{"AttributeName": "file_name", "KeyType": "HASH"}],
            "Projection": {"ProjectionType": "KEYS_ONLY"},
        }
    ]
    assert sorted(properties["AttributeDefinitions"], key=lambda a: a["AttributeName"]) == [
        {"AttributeName": "file_name", "AttributeType": "S"},
        {"AttributeName": "file_type", "AttributeType": "S"},
        {"AttributeName": "ingest_started", "AttributeType": "S"},
    ]


def test_ingest_status_table_has_point_in_time_recovery_and_is_deleted_with_the_stack(default_built):
    """The table can be restored to any point in the last 35 days, but a stack teardown deletes it."""
    table = only_resource(default_built.template, "AWS::DynamoDB::GlobalTable")
    assert table["Properties"]["Replicas"] == [
        {
            "GlobalSecondaryIndexes": [{"IndexName": "file_name_index"}],
            "PointInTimeRecoverySpecification": {"PointInTimeRecoveryEnabled": True},
            "Region": "us-west-2",
        }
    ]
    assert table["DeletionPolicy"] == "Delete"
    assert table["UpdateReplacePolicy"] == "Delete"


# ---- Lambda environments ----

def test_ingest_lambda_default_environment(default_built):
    """The ingest Lambda is told the domain endpoint, ingest bucket, status table and index, and its tuning defaults."""
    assert default_built.lambda_environment(default_built.ingest_lambda) == {
        "OPEN_SEARCH_ENDPOINT": DOMAIN_ENDPOINT,
        "BUCKET_NAME": {"Ref": default_built.id_of(default_built.ingest_bucket)},
        "INGEST_STATUS_TABLE": {"Ref": _table_id(default_built)},
        "INGEST_STATUS_FILE_NAME_GSI": "file_name_index",
        "CONSOLE_LOG_LEVEL": "INFO",
        "CHUNK_SIZE_MB": "5",
        "GENERATE_IDS": "1",
        "MAX_PROCESSES": "25",
        "MAX_FILE_SIZE_MB": "30",
        "OPENSEARCH_CLIENT_REQUEST_TIMEOUT": "60",
    }


def test_ingest_lambda_env_overrides_only_the_values_it_names():
    """A value in ingest_lambda_env replaces that default and leaves the others as they were."""
    built = _build(ingest_lambda_env={IngestLambdaEnv.MAX_PROCESSES: "4", IngestLambdaEnv.CONSOLE_LOG_LEVEL: "DEBUG"})
    environment = built.lambda_environment(built.ingest_lambda)
    assert environment["MAX_PROCESSES"] == "4"
    assert environment["CONSOLE_LOG_LEVEL"] == "DEBUG"
    assert environment["CHUNK_SIZE_MB"] == "5"
    assert len(environment) == 10


def test_dropbox_lambda_default_environment(default_built):
    """The dropbox Lambda is told both bucket names, its log level and the largest file it accepts."""
    assert default_built.lambda_environment(default_built.dropbox_lambda) == {
        "DROPBOX_BUCKET_NAME": {"Ref": default_built.id_of(default_built.dropbox_bucket)},
        "INGEST_BUCKET_NAME": {"Ref": default_built.id_of(default_built.ingest_bucket)},
        "CONSOLE_LOG_LEVEL": "INFO",
        "MAX_FILE_SIZE_MB": "30",
    }


def test_dropbox_lambda_env_overrides_only_the_values_it_names():
    """A value in dropbox_lambda_env replaces that default and leaves the others as they were."""
    built = _build(dropbox_lambda_env={DropboxLambdaEnv.MAX_FILE_SIZE_MB: "100"})
    assert built.lambda_environment(built.dropbox_lambda) == {
        "DROPBOX_BUCKET_NAME": {"Ref": built.id_of(built.dropbox_bucket)},
        "INGEST_BUCKET_NAME": {"Ref": built.id_of(built.ingest_bucket)},
        "CONSOLE_LOG_LEVEL": "INFO",
        "MAX_FILE_SIZE_MB": "100",
    }


# ---- Triggers ----

def test_each_lambda_reads_its_own_queue_one_message_at_a_time(default_built):
    """The dropbox Lambda reads the dropbox queue and the ingest Lambda the ingest queue, one file per invocation."""
    built = default_built
    found = built.template.find_resources("AWS::Lambda::EventSourceMapping")
    mappings = [mapping["Properties"] for mapping in found.values()]
    expected = [
        {
            "BatchSize": 1,
            "EventSourceArn": {"Fn::GetAtt": [built.id_of(built.dropbox_queue), "Arn"]},
            "FunctionName": {"Ref": built.id_of(built.dropbox_lambda)},
        },
        {
            "BatchSize": 1,
            "EventSourceArn": {"Fn::GetAtt": [built.id_of(built.ingest_queue), "Arn"]},
            "FunctionName": {"Ref": built.id_of(built.ingest_lambda)},
        },
    ]
    assert len(mappings) == 2
    for mapping in expected:
        assert mapping in mappings


# ---- Permissions ----

def _queue_consumer_statement(built: Built, queue: sqs.Queue) -> dict:
    """The statement CDK grants a Lambda to consume an SQS event source"""
    return {
        "Action": [
            "sqs:ReceiveMessage",
            "sqs:ChangeMessageVisibility",
            "sqs:GetQueueUrl",
            "sqs:DeleteMessage",
            "sqs:GetQueueAttributes",
        ],
        "Effect": "Allow",
        "Resource": {"Fn::GetAtt": [built.id_of(queue), "Arn"]},
    }


def _objects_in(built: Built, bucket: s3.Bucket) -> dict:
    """The ARN pattern for every object in a bucket the test made"""
    return {"Fn::Join": ["", [{"Fn::GetAtt": [built.id_of(bucket), "Arn"]}, "/*"]]}


def _only_statement_on(statements: list, resource) -> dict:
    """Return the one statement whose Resource is exactly `resource`"""
    found = [statement for statement in statements if statement["Resource"] == resource]
    assert len(found) == 1, statements
    return found[0]


def _assert_s3_statement_scope(statement: dict) -> None:
    """Check an S3 statement allows, with no condition, but leave its actions unchecked.

    Its actions include s3:*, which looks broader than the Lambdas need; that is reported for a decision rather
    than locked in by a test.
    """
    assert statement["Effect"] == "Allow"
    assert set(statement) == {"Action", "Effect", "Resource"}


def test_ingest_lambda_permissions(default_built):
    """The ingest Lambda may consume its queue, call the domain, and use the ingest bucket's objects and status table."""
    built = default_built
    table_arn = {"Fn::GetAtt": [_table_id(built), "Arn"]}
    statements = built.lambda_role_statements(built.ingest_lambda)
    s3_statement = _only_statement_on(statements, _objects_in(built, built.ingest_bucket))
    _assert_s3_statement_scope(s3_statement)
    assert_same_statements(
        [statement for statement in statements if statement is not s3_statement],
        [
            _queue_consumer_statement(built, built.ingest_queue),
            {"Action": "es:*", "Effect": "Allow", "Resource": f"{DOMAIN_ARN}/*"},
            {
                "Action": ["dynamodb:Query", "dynamodb:PutItem", "dynamodb:UpdateItem"],
                "Effect": "Allow",
                "Resource": [table_arn, {"Fn::Join": ["", [table_arn, "/*"]]}],
            },
        ],
    )


def test_dropbox_lambda_permissions(default_built):
    """The dropbox Lambda may consume its queue and use the objects of the dropbox and ingest buckets, nothing more."""
    built = default_built
    statements = built.lambda_role_statements(built.dropbox_lambda)
    s3_statement = _only_statement_on(
        statements, [_objects_in(built, built.ingest_bucket), _objects_in(built, built.dropbox_bucket)]
    )
    _assert_s3_statement_scope(s3_statement)
    assert_same_statements(
        [statement for statement in statements if statement is not s3_statement],
        [_queue_consumer_statement(built, built.dropbox_queue)],
    )


# ---- Backups ----

def test_no_backup_plan_without_a_vault(default_built):
    """Backups are opt-in: with no backup_vault there is no plan and no selection."""
    default_built.template.resource_count_is("AWS::Backup::BackupPlan", 0)
    default_built.template.resource_count_is("AWS::Backup::BackupSelection", 0)


def test_backup_vault_gets_a_daily_backup_of_the_status_table():
    """With a vault, the table is backed up daily at 02:00 UTC, moved to cold storage after 7 days, kept 120."""
    built = _build(with_backup_vault=True)
    vault_id = built.id_of(built.backup_vault)
    plan_id = only_resource_id(built.template, "AWS::Backup::BackupPlan")
    plan = only_resource(built.template, "AWS::Backup::BackupPlan")
    assert plan["Properties"]["BackupPlan"] == {
        "BackupPlanName": "IngestStatusTableBackupPlan",
        "BackupPlanRule": [
            {
                "CompletionWindowMinutes": 120,
                "Lifecycle": {"DeleteAfterDays": 120, "MoveToColdStorageAfterDays": 7},
                "RuleName": "IngestStatusTableBackupPlanRule0",
                "ScheduleExpression": "cron(0 2 * * ? *)",
                "StartWindowMinutes": 60,
                "TargetBackupVault": {"Fn::GetAtt": [vault_id, "BackupVaultName"]},
            }
        ],
    }
    selection = only_resource(built.template, "AWS::Backup::BackupSelection")
    assert selection["Properties"]["BackupPlanId"] == {"Fn::GetAtt": [plan_id, "BackupPlanId"]}
    assert selection["Properties"]["BackupSelection"]["Resources"] == [{"Fn::GetAtt": [_table_id(built), "Arn"]}]
    assert selection["Properties"]["BackupSelection"]["SelectionName"] == "IngestStatusTableSelection"
