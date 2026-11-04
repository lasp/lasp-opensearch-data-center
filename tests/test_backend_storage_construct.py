"""Tests for the buckets, queues and upload role that BackendStorageConstruct creates

The snapshot bucket's lifecycle rule is not tested here: #56 removes it and adds its own tests.
"""
# Standard
from typing import Tuple
# Installed
import aws_cdk as cdk
from aws_cdk.assertions import Template
import pytest
# Local
from lasp_opensearch_data_center.constructs.backend_storage import BackendStorageConstruct
from tests.construct_helpers import (
    ACCOUNT,
    assert_same_statements,
    only_resource,
    only_resource_id,
    role_policy_statements,
)

ENV = cdk.Environment(account=ACCOUNT, region="us-west-2")
BUCKET_NAMES = {
    "dropbox_bucket_name": "test-dropbox",
    "ingest_bucket_name": "test-ingest",
    "opensearch_snapshot_bucket_name": "test-snapshot",
}
# 14 days in seconds, the longest SQS keeps a message
FOURTEEN_DAYS_S = 14 * 24 * 60 * 60
# 20 minutes in seconds
TWENTY_MINUTES_S = 20 * 60


def _build(**kwargs) -> Tuple[cdk.Stack, BackendStorageConstruct, Template]:
    """Synthesize the construct with BUCKET_NAMES and any other arguments given"""
    stack = cdk.Stack(cdk.App(), "TestStack", env=ENV)
    storage = BackendStorageConstruct(stack, "Storage", **{**BUCKET_NAMES, **kwargs})
    return stack, storage, Template.from_stack(stack)


@pytest.fixture(scope="module")
def built() -> Tuple[cdk.Stack, BackendStorageConstruct, Template]:
    """The construct built with BUCKET_NAMES and every optional argument left at its default"""
    return _build()


def test_each_bucket_attribute_has_its_own_name(built):
    """Callers wire each bucket by attribute (dropbox_bucket and so on), so each must carry its own name."""
    stack, storage, template = built
    template.resource_count_is("AWS::S3::Bucket", 3)
    for bucket, name in [
        (storage.dropbox_bucket, "test-dropbox"),
        (storage.ingest_bucket, "test-ingest"),
        (storage.opensearch_snapshot_bucket, "test-snapshot"),
    ]:
        bucket_id = only_resource_id(template, "AWS::S3::Bucket", {"BucketName": name})
        assert stack.resolve(bucket.bucket_name) == {"Ref": bucket_id}


def test_buckets_are_deleted_with_the_stack(built):
    """Every bucket uses RemovalPolicy.DESTROY, so a stack teardown removes them once they are empty."""
    _, _, template = built
    for bucket in template.find_resources("AWS::S3::Bucket").values():
        assert bucket["DeletionPolicy"] == "Delete"
        assert bucket["UpdateReplacePolicy"] == "Delete"


def test_versioning_is_off_by_default(built):
    """Without enable_bucket_versioning, no bucket keeps old object versions."""
    _, _, template = built
    for bucket in template.find_resources("AWS::S3::Bucket").values():
        assert "VersioningConfiguration" not in bucket["Properties"]


def test_enable_bucket_versioning_turns_it_on_for_every_bucket():
    """enable_bucket_versioning=True reaches all three buckets, not just one."""
    _, _, template = _build(enable_bucket_versioning=True)
    buckets = template.find_resources("AWS::S3::Bucket")
    assert len(buckets) == 3
    for bucket in buckets.values():
        assert bucket["Properties"]["VersioningConfiguration"] == {"Status": "Enabled"}


@pytest.mark.parametrize("dead_letter_queue_name", ["DropboxDeadLetterQueue", "DeadLetterQueue"])
def test_dead_letter_queues_keep_messages_14_days(built, dead_letter_queue_name):
    """A failed file's message waits in its dead-letter queue for the longest SQS allows, 14 days."""
    _, _, template = built
    queue = only_resource(template, "AWS::SQS::Queue", {"QueueName": dead_letter_queue_name})
    assert queue["Properties"] == {"QueueName": dead_letter_queue_name, "MessageRetentionPeriod": FOURTEEN_DAYS_S}


def test_dropbox_queue_hides_messages_20_minutes_and_dead_letters_after_one_receive(built):
    """One failed attempt sends a dropbox message to its DLQ, and a message in progress stays hidden 20 minutes."""
    stack, storage, template = built
    dead_letter_queue_id = only_resource_id(template, "AWS::SQS::Queue", {"QueueName": "DropboxDeadLetterQueue"})
    queue_id = only_resource_id(template, "AWS::SQS::Queue", {"QueueName": "DropboxQueue"})
    # The construct's dropbox_queue attribute is this queue
    assert stack.resolve(storage.dropbox_queue.queue_arn) == {"Fn::GetAtt": [queue_id, "Arn"]}
    queue = only_resource(template, "AWS::SQS::Queue", {"QueueName": "DropboxQueue"})
    assert queue["Properties"] == {
        "QueueName": "DropboxQueue",
        "VisibilityTimeout": TWENTY_MINUTES_S,
        "RedrivePolicy": {"deadLetterTargetArn": {"Fn::GetAtt": [dead_letter_queue_id, "Arn"]}, "maxReceiveCount": 1},
    }


def test_ingest_queue_hides_messages_20_minutes_and_dead_letters_after_one_receive(built):
    """One failed attempt sends an ingest message to its DLQ, and a message in progress stays hidden 20 minutes."""
    stack, storage, template = built
    dead_letter_queue_id = only_resource_id(template, "AWS::SQS::Queue", {"QueueName": "DeadLetterQueue"})
    queue_id = only_resource_id(template, "AWS::SQS::Queue", {"QueueName": "IngestQueue"})
    # The construct's ingest_queue attribute is this queue
    assert stack.resolve(storage.ingest_queue.queue_arn) == {"Fn::GetAtt": [queue_id, "Arn"]}
    queue = only_resource(template, "AWS::SQS::Queue", {"QueueName": "IngestQueue"})
    assert queue["Properties"] == {
        "QueueName": "IngestQueue",
        "VisibilityTimeout": TWENTY_MINUTES_S,
        "RedrivePolicy": {"deadLetterTargetArn": {"Fn::GetAtt": [dead_letter_queue_id, "Arn"]}, "maxReceiveCount": 1},
    }


def test_only_the_dropbox_and_ingest_buckets_send_notifications(built):
    """The snapshot bucket must not feed either queue; OpenSearch writes many objects there every day."""
    _, _, template = built
    template.resource_count_is("Custom::S3BucketNotifications", 2)


def test_dropbox_bucket_queues_new_objects_under_received_files_only(built):
    """Only files created under received_files/ reach the dropbox queue, not objects elsewhere in the bucket."""
    stack, storage, template = built
    notifications = only_resource(
        template, "Custom::S3BucketNotifications", {"BucketName": stack.resolve(storage.dropbox_bucket.bucket_name)}
    )
    assert notifications["Properties"]["NotificationConfiguration"] == {
        "QueueConfigurations": [
            {
                "Events": ["s3:ObjectCreated:*"],
                "Filter": {"Key": {"FilterRules": [{"Name": "prefix", "Value": "received_files/"}]}},
                "QueueArn": stack.resolve(storage.dropbox_queue.queue_arn),
            }
        ]
    }


def test_ingest_bucket_queues_every_new_object(built):
    """Every object created in the ingest bucket goes to the ingest queue, whatever its key."""
    stack, storage, template = built
    notifications = only_resource(
        template, "Custom::S3BucketNotifications", {"BucketName": stack.resolve(storage.ingest_bucket.bucket_name)}
    )
    assert notifications["Properties"]["NotificationConfiguration"] == {
        "QueueConfigurations": [
            {"Events": ["s3:ObjectCreated:*"], "QueueArn": stack.resolve(storage.ingest_queue.queue_arn)}
        ]
    }


def test_upload_role_can_be_assumed_from_this_account_only(built):
    """The dropbox upload role trusts this account's IAM principals, and no other account or service."""
    stack, storage, template = built
    role = only_resource(template, "AWS::IAM::Role", {"RoleName": "dropbox-upload-role"})
    assert role["Properties"]["Description"] == "Role that allows csv file uploads to the S3 dropbox"
    assert role["Properties"]["AssumeRolePolicyDocument"]["Statement"] == [
        {
            "Action": "sts:AssumeRole",
            "Effect": "Allow",
            "Principal": {"AWS": {"Fn::Join": ["", ["arn:", {"Ref": "AWS::Partition"}, f":iam::{ACCOUNT}:root"]]}},
        }
    ]
    assert stack.resolve(storage.dropbox_upload_role.role_name) == {
        "Ref": only_resource_id(template, "AWS::IAM::Role", {"RoleName": "dropbox-upload-role"})
    }


def test_upload_policy_allows_put_and_delete_in_the_dropbox_bucket_only(built):
    """Uploaders may list bucket names and put or delete objects in the dropbox bucket, and nothing more."""
    stack, storage, template = built
    role_id = only_resource_id(template, "AWS::IAM::Role", {"RoleName": "dropbox-upload-role"})
    dropbox_arn = stack.resolve(storage.dropbox_bucket.bucket_arn)
    assert_same_statements(
        role_policy_statements(template, role_id),
        [
            {
                "Action": ["s3:GetBucketLocation", "s3:ListAllMyBuckets"],
                "Effect": "Allow",
                "Resource": "arn:aws:s3:::*",
            },
            {
                "Action": ["s3:PutObject", "s3:DeleteObject"],
                "Effect": "Allow",
                "Resource": [dropbox_arn, {"Fn::Join": ["", [dropbox_arn, "/*"]]}],
            },
        ],
    )
