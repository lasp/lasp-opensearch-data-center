"""CDK resources for ingesting data dropped into the ingest dropbox"""
# Standard
from pathlib import Path

# Installed
from constructs import Construct
from aws_cdk import (
    Environment,
    Construct,
    RemovalPolicy,
    aws_s3 as s3,
    aws_sqs as sqs,
    Duration,
    aws_s3_notifications as s3_notify,
)


class BackendStorage(Construct):
    """Stack containing resources used during ingest processing"""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        account_type: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id)

        # DROPBOX BUCKET for all rack files
        self.dropbox_bucket = s3.Bucket(
            self,
            "DropboxBucket",
            bucket_name=f"{account_type}-liberaitdc-dropbox",
            removal_policy=RemovalPolicy.DESTROY,
            versioned=True,
        )

        # NOTE: We arguably don't need this queue but I don't think it will do any harm and may provide visibility.
        # Create DLQ for dropbox
        dropbox_dlq = sqs.Queue(
            # Store for 14 days which is max retention
            self,
            id="DropboxDeadLetterQueue",
            queue_name="LiberaDropboxDeadLetterQueue",
            retention_period=Duration.days(14),
        )
        # 1 Lambda attempt from standard queue before moving failed message to DLQ
        dropbox_dead_letter_queue = sqs.DeadLetterQueue(
            max_receive_count=1, queue=dropbox_dlq
        )

        self.dropbox_queue = sqs.Queue(
            self,
            id="dropbox_queue",
            queue_name="LiberaDropboxQueue",
            # We don't want the message in the queue visable to any other
            # Lambda functions once it has been pulled for processing, this provides a buffer
            # to ensure a single message is not processed by multiple Lambda functions
            visibility_timeout=Duration.minutes(20),
            dead_letter_queue=dropbox_dead_letter_queue,
        )

        # Send files from dropbox bucket into SQS
        dropbox_bucket_notification = s3_notify.SqsDestination(self.dropbox_queue)
        dropbox_bucket_notification.bind(self, self.dropbox_bucket)

        # Add s3 Event notification for all files appearing in dropbox bucket
        self.dropbox_bucket.add_object_created_notification(dropbox_bucket_notification)

        # INGEST BUCKET for all pre-processed objects
        self.ingest_bucket = s3.Bucket(
            self,
            "IngestBucket",
            bucket_name=f"{account_type}-liberaitdc-ingest",
            removal_policy=RemovalPolicy.DESTROY,
            versioned=True,
        )

        # Create DLQ
        ingest_dlq = sqs.Queue(
            # Store for 14 days which is max retention
            self,
            id="DeadLetterQueue",
            queue_name="LiberaDeadLetterQueue",
            retention_period=Duration.days(14),
        )
        # 1 Lambda attempt from standard queue before moving failed message to DLQ
        dead_letter_queue = sqs.DeadLetterQueue(max_receive_count=1, queue=ingest_dlq)

        self.ingest_queue = sqs.Queue(
            self,
            id="ingest_queue",
            queue_name="LiberaIngestQueue",
            # We don't want the message in the queue visable to any other
            # Lambda functions once it has been pulled for processing, this provides a buffer
            # to ensure a single message is not processed by multiple Lambda functions
            visibility_timeout=Duration.minutes(20),
            dead_letter_queue=dead_letter_queue,
        )

        # Send files from ingest bucket into SQS
        ingest_bucket_notification = s3_notify.SqsDestination(self.ingest_queue)
        ingest_bucket_notification.bind(self, self.ingest_bucket)

        # Add s3 Event notification for all files appearing in ingest bucket
        self.ingest_bucket.add_object_created_notification(ingest_bucket_notification)

        # S3 bucket to store the snapshot data
        # The data is stored in native Lucene format
        # TODO: Determine lifecycle policy, retention on snapshots, for now indefinite
        self.liberaitdc_opensearch_snapshot_bucket = s3.Bucket(
            self,
            "OSSnapshotBucket",
            bucket_name=f"{account_type}-liberaitdc-opensearch-manual-snapshot",
            removal_policy=RemovalPolicy.DESTROY,
            versioned=True,
        )
