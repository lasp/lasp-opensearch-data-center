"""Standard set of CDK resources for data storage in the data center back end"""
# Installed
from constructs import Construct
from aws_cdk import (
    RemovalPolicy,
    aws_s3 as s3,
    aws_sqs as sqs,
    Duration,
    aws_s3_notifications as s3_notify,
)


class BackendStorageConstruct(Construct):
    """Construct containing standard resources used by the data center back end, including notification mechanisms
    for data arrival and queuing of arrival events
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        dropbox_bucket_name: str,
        ingest_bucket_name: str,
        opensearch_snapshot_bucket_name: str,
    ) -> None:
        """Construct init

        :param scope: Construct
            The scope in which this Construct is instantiated, usually the `self` inside a Stack.
        :param construct_id: str
            ID for this construct instance, e.g. "MyBackendStorageConstruct"
        :param dropbox_bucket_name: str
            Name of the dropbox storage bucket
        :param ingest_bucket_name: str
            Name of the ingest storage bucket
        :param opensearch_snapshot_bucket_name: str
            Name of the bucket used to store opensearch index snapshots
        """
        super().__init__(scope, construct_id)

        # DROPBOX BUCKET for all rack files
        self.dropbox_bucket = s3.Bucket(
            self,
            "DropboxBucket",
            bucket_name=dropbox_bucket_name,
            removal_policy=RemovalPolicy.DESTROY,
            versioned=True,
        )

        # NOTE: We arguably don't need this queue but I don't think it will do any harm and may provide visibility.
        # Create DLQ for dropbox
        dropbox_dlq = sqs.Queue(
            # Store for 14 days which is max retention
            self,
            id="DropboxDeadLetterQueue",
            queue_name="DropboxDeadLetterQueue",
            retention_period=Duration.days(14),
        )
        # 1 Lambda attempt from standard queue before moving failed message to DLQ
        dropbox_dead_letter_queue = sqs.DeadLetterQueue(
            max_receive_count=1, queue=dropbox_dlq
        )

        self.dropbox_queue = sqs.Queue(
            self,
            id="dropbox_queue",
            queue_name="DropboxQueue",
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
            bucket_name=ingest_bucket_name,
            removal_policy=RemovalPolicy.DESTROY,
            versioned=True,
        )

        # Create DLQ
        ingest_dlq = sqs.Queue(
            # Store for 14 days which is max retention
            self,
            id="DeadLetterQueue",
            queue_name="DeadLetterQueue",
            retention_period=Duration.days(14),
        )
        # 1 Lambda attempt from standard queue before moving failed message to DLQ
        dead_letter_queue = sqs.DeadLetterQueue(max_receive_count=1, queue=ingest_dlq)

        self.ingest_queue = sqs.Queue(
            self,
            id="ingest_queue",
            queue_name="IngestQueue",
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
        self.opensearch_snapshot_bucket = s3.Bucket(
            self,
            "OSSnapshotBucket",
            bucket_name=opensearch_snapshot_bucket_name,
            removal_policy=RemovalPolicy.DESTROY,
            versioned=True,
            lifecycle_rules=[
                # Define the lifecycle rule to delete objects after 90 days
                s3.LifecycleRule(
                    expiration=Duration.days(90)
                )
            ]
        )
