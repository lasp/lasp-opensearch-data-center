"""Standard set of CDK resources for data storage in the data center back end"""
# Installed
from constructs import Construct
from aws_cdk import (
    RemovalPolicy,
    aws_iam,
    aws_s3 as s3,
    aws_sqs as sqs,
    Duration,
    aws_s3_notifications as s3_notify,
)

from aws_cdk.aws_s3 import NotificationKeyFilter


class BackendStorageConstruct(Construct):
    """Construct containing standard resources used by the data center back end, including notification mechanisms
    for data arrival and queuing of arrival events
    """

    def __init__(
            self,
            scope: Construct,
            construct_id: str,
            *,
            dropbox_bucket_name: str,
            ingest_bucket_name: str,
            opensearch_snapshot_bucket_name: str,
            enable_bucket_versioning: bool = False
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
        :param enable_bucket_versioning: bool
            Set to true to allow versioning for all backend storage buckets. Defaults to false.
        """
        super().__init__(scope, construct_id)

        # DROPBOX BUCKET for all rack files
        self.dropbox_bucket = s3.Bucket(
            self,
            "DropboxBucket",
            bucket_name=dropbox_bucket_name,
            removal_policy=RemovalPolicy.DESTROY,
            versioned=enable_bucket_versioning,
        )

        # INGEST BUCKET for all pre-processed objects
        self.ingest_bucket = s3.Bucket(
            self,
            "IngestBucket",
            bucket_name=ingest_bucket_name,
            removal_policy=RemovalPolicy.DESTROY,
            versioned=enable_bucket_versioning,
        )

        # S3 bucket to store the snapshot data
        # The data is stored in native Lucene format
        self.opensearch_snapshot_bucket = s3.Bucket(
            self,
            "OSSnapshotBucket",
            bucket_name=opensearch_snapshot_bucket_name,
            removal_policy=RemovalPolicy.DESTROY,
            versioned=enable_bucket_versioning,
            lifecycle_rules=[
                # Define the lifecycle rule to delete objects after 90 days
                s3.LifecycleRule(
                    expiration=Duration.days(90)
                )
            ]
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
        self.dropbox_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3_notify.SqsDestination(self.dropbox_queue),
            s3.NotificationKeyFilter(prefix="received_files/")
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
            # We don't want the message in the queue visible to any other
            # Lambda functions once it has been pulled for processing, this provides a buffer
            # to ensure a single message is not processed by multiple Lambda functions
            visibility_timeout=Duration.minutes(20),
            dead_letter_queue=dead_letter_queue,
        )

        # Send files from ingest bucket into SQS
        self.ingest_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3_notify.SqsDestination(self.ingest_queue)
        )

        # Define the role and policy names to allow CSV files to be uploaded to the S3 dropbox
        # The user will be created in the AWS Management Console
        # Permission to assume the role will be granted to the user at the time of creation
        dropbox_upload_role_name = "dropbox-upload-role"
        dropbox_upload_policy_name = "dropbox-upload-policy"

        # Create the IAM role
        self.dropbox_upload_role = aws_iam.Role(
            self, dropbox_upload_role_name,
            role_name=dropbox_upload_role_name,
            description="Role that allows csv file uploads to the S3 dropbox",
            # Allow any user in this account with the proper permissions, to assume this role
            assumed_by=aws_iam.AccountRootPrincipal()
        )

        # Create the IAM policy to allow access to the S3 dropbox bucket
        self.frontend_iam_policy = aws_iam.ManagedPolicy(
            self,
            dropbox_upload_policy_name,
            description="Dropbox upload policy for csv files",
            managed_policy_name=dropbox_upload_policy_name,
            roles=[self.dropbox_upload_role],
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
                    # S3 dropbox bucket and object permissions
                    effect=aws_iam.Effect.ALLOW,
                    actions=[
                        "s3:PutObject",
                        "s3:DeleteObject",
                    ],
                    resources=[
                        self.dropbox_bucket.bucket_arn,
                        self.dropbox_bucket.bucket_arn + "/*",
                    ],
                ),
            ],
        )