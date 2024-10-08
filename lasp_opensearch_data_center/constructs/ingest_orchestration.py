"""Construct for creating an ingest processing system for data to be ingested into OpenSearch

This construct must be configured with custom Lambda functions for managing input data and
ingesting data into an existing OpenSearch instance.

During ingest, data is placed into a Dropbox Bucket. A (custom) Lambda function responds to new files events
and moves those files into the Ingest Bucket. This allows users to organize and validate input data in the
Ingest Bucket, catching errors like invalid file names or formats.

When data lands in the Ingest Bucket, a second Lambda function called the Ingest Lambda ingests the file contents
into the OpenSearch instance.
"""
# Standard
from typing import Optional
# Installed
from constructs import Construct
from aws_cdk import (
    Duration,
    RemovalPolicy,
    aws_iam as iam,
    aws_events as events,
    aws_backup as backup,
    aws_s3 as s3,
    aws_sqs as sqs,
    aws_lambda as lambda_,
    aws_lambda_event_sources as lambda_event_source,
    aws_dynamodb as ddb,
    aws_opensearchservice as opensearch
)
# Local
from lasp_opensearch_data_center.constructs.constants import (
    INGEST_STATUS_TABLE_NAME,
    INGEST_STATUS_TABLE_PK,
    INGEST_STATUS_TABLE_SK,
    INGEST_STATUS_FILE_NAME_GSI_PK,
    INGEST_STATUS_FILE_NAME_GSI_NAME,
    DropboxLambdaEnv,
    IngestLambdaEnv
)


class IngestProcessingConstruct(Construct):
    """Construct containing resources used to orchestrate dropbox and ingest processing"""

    def __init__(
            self,
            scope: Construct,
            construct_id: str,
            *,
            open_search_domain: opensearch.Domain,
            dropbox_bucket: s3.Bucket,
            ingest_bucket: s3.Bucket,
            ingest_queue: sqs.Queue,
            dropbox_queue: sqs.Queue,
            dropbox_lambda: lambda_.Function,
            ingest_lambda: lambda_.Function,
            dropbox_lambda_env: Optional[dict] = None,
            ingest_lambda_env: Optional[dict] = None,
            backup_vault: Optional[backup.BackupVault] = None,
    ) -> None:
        """Construct init for ingest processing orchestration construct

        Parameters
        ----------
        scope : Construct
            The scope in which this Construct is instantiated, usually the `self` inside a Stack.
        construct_id : str
            ID for this construct instance, e.g. "MyOpenSearchConstruct".
        open_search_domain : opensearch.Domain
            The domain where opensearch lives. Used to provide environment variables to the Lambda functions.
        backup_vault : Optional[backup.BackupVault]
            Default None (no backups). Provide a backup vault to back up the ingest status table.
        dropbox_bucket : s3.Bucket
            Dropbox Bucket for incoming data. Used to provide environment variables to the Lambda functions.
        ingest_bucket : s3.Bucket
            Ingest Bucket for starting ingest processing. Used to provide environment variables to the Lambda functions.
        dropbox_queue : sqs.Queue
            Simple queue for incoming data.
        ingest_queue : sqs.Queue
            Simple queue for data ready to be ingested.
        dropbox_lambda : lambda_.Function
            Lambda function for validating and organizing incoming data from the Dropbox Bucket to the Ingest Bucket
        ingest_lambda : lambda_.Function
            Lambda function for ingest data into opensearch.
        dropbox_lambda_env : Optional[dict]
            Environment variables for the Dropbox Lambda function. These variables overwrite the defaults assigned
            to the function internally to this construct. See constants.DropboxLambdaEnv.
        ingest_lambda_env : Optional[dict]
            Environment variables for the Ingest Lambda function. These variables overwrite the defaults assigned
            to the function internally to this construct. See constants.IngestLambdaEnv.
        backup_vault : Optional[backup.BackupVault]
            Backup vault to use for backing up the ingest status table.
        """
        super().__init__(scope, construct_id)

        # Orchestration for ingesting files from Ingest Bucket into OpenSearch
        self._create_ingest_resources(
            open_search_domain=open_search_domain,
            ingest_bucket=ingest_bucket,
            ingest_queue=ingest_queue,
            ingest_lambda=ingest_lambda,
            ingest_lambda_env=ingest_lambda_env
        )

        # Orchestration for validating and moving files from Dropbox Bucket to Ingest Bucket
        self._create_dropbox_resources(
            dropbox_bucket=dropbox_bucket,
            ingest_bucket=ingest_bucket,
            dropbox_queue=dropbox_queue,
            dropbox_lambda=dropbox_lambda,
            dropbox_lambda_env=dropbox_lambda_env
        )

        # Conditionally set up backups for the Ingest Status DDB Table
        if backup_vault is not None:
            self._setup_ingest_status_table_backup(backup_vault)

    def _create_dropbox_resources(
        self,
        dropbox_bucket: s3.Bucket,
        ingest_bucket: s3.Bucket,
        dropbox_queue: sqs.Queue,
        dropbox_lambda: lambda_.Function,
        dropbox_lambda_env: Optional[dict] = None
    ):
        """Sets up the Lambda trigger based on data arrival and configures environment variables in the Dropbox
        Lambda function.

        Parameters
        ----------
        dropbox_bucket : s3.Bucket
            Dropbox Bucket where data is initially placed for processing
        ingest_bucket : s3.Bucket
            Ingest Bucket where the Lambda should place the validated and organized files to trigger ingestion
            into OpenSearch
        dropbox_lambda : lambda_.Function
            Lambda function for validating and moving files from the Dropbox Bucket to the Ingest Bucket. This function
            is modified with environment variables containing the Dropbox Bucket and Ingest Bucket names.
        dropbox_lambda_env : Optional[dict]
            User defined environment variables for the Dropbox Lambda. These overwrite the defaults when they are
            assigned to the Function.
        """
        # Add standard environment variables to the Dropbox Lambda for use by the runtime code
        dropbox_lambda_environment = {
            DropboxLambdaEnv.DROPBOX_BUCKET_NAME: dropbox_bucket.bucket_name,
            DropboxLambdaEnv.INGEST_BUCKET_NAME: ingest_bucket.bucket_name,
            DropboxLambdaEnv.CONSOLE_LOG_LEVEL: "INFO",
        }
        if dropbox_lambda_env:
            # Update the default environment variables with the user specified environment variables
            dropbox_lambda_environment = {**dropbox_lambda_environment, **dropbox_lambda_env}

        for env_var, value in dropbox_lambda_environment.items():
            dropbox_lambda.add_environment(env_var.value, value)

        # S3
        dropbox_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["s3:*", "s3:ListObjects", "s3:GetObject"],
                resources=[
                    f"{ingest_bucket.bucket_arn}/*",
                    f"{dropbox_bucket.bucket_arn}/*",
                ],
            )
        )

        # SQS event trigger for dropbox lambda
        dropbox_lambda.add_event_source(
            lambda_event_source.SqsEventSource(
                dropbox_queue,
                batch_size=1,
            )
        )

    def _create_ingest_resources(
        self,
        open_search_domain: opensearch.Domain,
        ingest_bucket: s3.Bucket,
        ingest_queue: sqs.Queue,
        ingest_lambda: lambda_.Function,
        ingest_lambda_env: Optional[dict] = None
    ):
        """Creates orchestration resources required for ingesting data into OpenSearch.

        Ingest Lambda function must be defined outside the scope of this Construct for customization by users.

        Parameters
        ----------
        open_search_domain : opensearch.Domain
            The opensearch domain, e.g. from the lasp_opensearch_data_center.opensearch.OpenSearchConstruct
        ingest_bucket : s3.Bucket
            Storage for files to be ingested
        ingest_queue : sqs.Queue
            SQS Queue for ingesting files
        ingest_lambda : lambda_.Function
            Lambda that runs ingest logic (externally defined)
        ingest_lambda_env : Optional[dict]
            Optional dictionary containing user defined Lambda environment variables. These are merged with the
            default environment variables and added to the Ingest Lambda.
        """

        # Dynamo DB table to record ingest state of incoming files
        # PK is set to filename since we expect only one entry per unique filename
        # TODO: Standardize this schema and provide helper functions for writing basic status records
        #  Also write up documentation on how this table's keys are named and why
        self.ingest_status_table = ddb.TableV2(
            self,
            "IngestStatusTable",
            table_name=INGEST_STATUS_TABLE_NAME,
            removal_policy=RemovalPolicy.DESTROY,
            point_in_time_recovery=True,
            # A partition/hash key is required for Dynamo to determine where the data is stored
            partition_key=ddb.Attribute(
                name=INGEST_STATUS_TABLE_PK, type=ddb.AttributeType.STRING
            ),
            # The sort key allows the data to be stored in sorted order within each partition
            sort_key=ddb.Attribute(
                name=INGEST_STATUS_TABLE_SK, type=ddb.AttributeType.STRING
            ),
        )

        # The ingest code checks for existing file names prior to ingest
        # This Global secondary index will enable the ingest
        # code to quickly determine if a file_name has already been ingested

        # Any changes to the GSI may require a stack destroy than deploy, I received this error:
        # "Cannot update GSI's properties other than Provisioned Throughput and
        # Contributor Insights Specification. You can create a new GSI with a different name."
        self.ingest_status_table.add_global_secondary_index(
            index_name=INGEST_STATUS_FILE_NAME_GSI_NAME,
            # Single PK partition key
            partition_key=ddb.Attribute(
                name=INGEST_STATUS_FILE_NAME_GSI_PK, type=ddb.AttributeType.STRING
            ),
            # Only include the partition key in the GSI
            projection_type=ddb.ProjectionType.KEYS_ONLY,
        )

        # Add standard environment variables to the ingest Lambda function
        ingest_lambda_environment = {
            IngestLambdaEnv.OPEN_SEARCH_ENDPOINT: open_search_domain.domain_endpoint,
            IngestLambdaEnv.BUCKET_NAME: ingest_bucket.bucket_name,
            IngestLambdaEnv.INGEST_STATUS_TABLE: self.ingest_status_table.table_name,
            IngestLambdaEnv.INGEST_STATUS_FILE_NAME_GSI: INGEST_STATUS_FILE_NAME_GSI_NAME,
            IngestLambdaEnv.CONSOLE_LOG_LEVEL: "INFO",
            IngestLambdaEnv.CHUNK_SIZE_MB: "5",
            IngestLambdaEnv.GENERATE_IDS: "1",
            IngestLambdaEnv.MAX_PROCESSES: "25",
            IngestLambdaEnv.MAX_FILE_SIZE_MB: "100",
            IngestLambdaEnv.OPENSEARCH_CLIENT_REQUEST_TIMEOUT: "60",
        }
        if ingest_lambda_env:
            # Update the default environment variables with the user specified environment variables
            ingest_lambda_environment = {**ingest_lambda_environment, **ingest_lambda_env}

        for env_var, value in ingest_lambda_environment.items():
            ingest_lambda.add_environment(env_var.value, value)

        # Add SQS trigger for ingest Lambda
        ingest_lambda.add_event_source(
            lambda_event_source.SqsEventSource(
                ingest_queue,
                batch_size=1,
            )
        )

        # Add permissions for Lambda to access:
        # OpenSearch
        ingest_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["es:*"],
                resources=[f"{open_search_domain.domain_arn}/*"],
            )
        )
        # Add permissions for Ingest Lambda to access the Ingest Bucket
        ingest_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["s3:*", "s3:ListObjects", "s3:GetObject"],
                resources=[f"{ingest_bucket.bucket_arn}/*"],
            )
        )
        # Add permissions for the Ingest Lambda to access the DynamoDB table for tracking data ingestion
        ingest_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "dynamodb:Query",
                    "dynamodb:PutItem",
                    "dynamodb:UpdateItem",
                ],
                resources=[
                    # Provide access to the table
                    f"{self.ingest_status_table.table_arn}",
                    # Provide access to any indexes
                    f"{self.ingest_status_table.table_arn}/*",
                ],
            )
        )

    def _setup_ingest_status_table_backup(
            self,
            backup_vault: backup.BackupVault
    ):
        """Setup recurring/scheduled Dynamo DB backups for the Ingest Status Table

        A new IAM role is created by default for the AWS backup service to assume, so an explicit one is not needed

        Parameters
        ----------
        backup_vault : backup.BackupVault
            Externally defined Backup Vault for storing Ingest Status Table backups
        """
        # Backup Plan - rules for backup schedule, retention, and tiering configs
        backup_plan = backup.BackupPlan(
            self,
            "IngestStatusTableBackupPlan",
            backup_vault=backup_vault
        )

        # Add a backup plan to the DynamoDB table
        backup_plan.add_rule(
            backup.BackupPlanRule(
                # How long the backup must complete in after started, or it will terminate
                # Dynamo DB backups are expected to be snapshots and take less than 1 minute
                completion_window=Duration.hours(2),
                # Duration after a backup is scheduled before a job is canceled if it doesn’t start successfully.
                start_window=Duration.hours(1),
                # Take a backup at 2am UTC every day
                schedule_expression=events.Schedule.cron(day="*", hour="2", minute="0"),
                # All initial backups are "warm", we'll tier them to cold storage
                # after 7 days
                move_to_cold_storage_after=Duration.days(7),
                delete_after=Duration.days(120)
            )
        )

        # Add the DynamoDB table to the backup plan
        backup_plan.add_selection(
            "IngestStatusTableSelection",
            resources=[
                backup.BackupResource.from_dynamo_db_table(self.ingest_status_table)
            ],
        )
