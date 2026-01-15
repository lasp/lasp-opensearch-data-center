"""Open Search Stack"""

# Standard
from pathlib import Path
from typing import Optional
import warnings

# Installed
from constructs import Construct
from aws_cdk import (
    CfnResource,
    Environment,
    Duration,
    aws_opensearchservice as opensearch,
    aws_ec2 as ec2,
    aws_route53 as route53,
    RemovalPolicy,
    aws_certificatemanager as acm,
    aws_iam as iam,
    aws_s3 as s3,
    aws_lambda as lambda_,
    aws_events as events,
    aws_events_targets as targets,
    aws_ecr_assets as ecr_assets,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_logs as logs,
    aws_sns as sns,
    Duration
)
from aws_cdk.aws_stepfunctions import DefinitionBody

# Local
from lasp_opensearch_data_center.constructs.constants import (

    OPENSEARCH_SNAPSHOT_REPO_NAME,
)
from lasp_opensearch_data_center.constructs.constants import (
    IngestLambdaEnv
)

class OpenSearchConstruct(Construct):
    """OpenSearch Construct to create the Open Search Domain and cluster nodes

    NOTE: This construct takes ~20-40 minutes to deploy/destroy the OpenSearch service.
    Access to the website GUI is available via https://search.{hosted_zone.zone_name}/_dashboards/app/home#/
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        environment: Environment,
        hosted_zone: route53.HostedZone,
        certificate: acm.Certificate,
        opensearch_snapshot_bucket: s3.Bucket,
        opensearch_domain_name: str,
        opensearch_version: opensearch.EngineVersion = opensearch.EngineVersion.open_search(
            "2.9"
        ),
        opensearch_zone_awareness: Optional[opensearch.ZoneAwarenessConfig] = None,
        opensearch_data_node_instance_type: str = "t3.medium.search",
        opensearch_data_node_count: int = 1,
        opensearch_manager_node_instance_type: str = "t3.medium.search",
        opensearch_manager_node_count: int = 1,
        opensearch_ip_access_range: list[str] = ["127.0.0.1/32"],
        opensearch_volume_size: int = 50,
        snapshot_repo_name: str = OPENSEARCH_SNAPSHOT_REPO_NAME,
        removal_policy: RemovalPolicy = RemovalPolicy.RETAIN,
        snapshot_lambda: Optional[lambda_.Function] = None,
        snapshot_schedule: events.Schedule = events.Schedule.cron(
            minute="0", hour="9", month="*", week_day="*", year="*"
        ),
    ) -> None:
        """
        Construct init.

        Parameters
        ----------
        scope : Construct
            The scope in which this Construct is instantiated, usually the `self` inside a Stack.
        construct_id : str
            ID for this construct instance, e.g. "MyOpenSearchConstruct".
        environment : Environment
            AWS environment (account and region).
        hosted_zone : route53.HostedZone
            Hosted zone to host the OpenSearch instance. Associated with a domain name (e.g. `my-domain-name.net`).
            Can be sourced from a lasp_opensearch_data_center CertificateStack.
        certificate : acm.Certificate
            Pre-defined certificate for OpenSearch. This is likely to be used elsewhere, so it is left to the user
            to define it outside of this Construct. Can be sourced from a lasp_opensearch_data_center CertificateStack.
        opensearch_snapshot_bucket : s3.Bucket
            S3 bucket in which to store periodic snapshots of OpenSearch indexes. Snapshots are taken using a daily
            scheduled Lambda that makes an API call to take the snapshot. See `docker_context_path` if you want
            to customize this Lambda function. This is required so that you can manage your persistent storage
            independent of your OS cluster deployment.
        opensearch_domain_name : str
            Name of the OpenSearch domain, e.g. (`opensearch-testing`). Required to name the OpenSearch Domain
            but does not affect access URLs.
        opensearch_version : str, optional
            Version of OpenSearch to deploy, e.g. "2.5". Default is "2.9".
        opensearch_zone_awareness : opensearch.ZoneAwarenessConfig, optional
            AWS can optionally distribute OpenSearch nodes across multiple AZs to increase availability.
            Default is None (no zone awareness).
        opensearch_data_node_instance_type : str, optional
            EC2 instance type for OpenSearch data nodes, which handle indexing and searching.
            These nodes require significant resources.
            Default is `t3.medium.search` to minimize initial deployment costs.
            For a full list of supported instance types and recommendations, see:
            https://docs.aws.amazon.com/opensearch-service/latest/developerguide/supported-instance-types.html
        opensearch_data_node_count : int, optional
            Number of OpenSearch data nodes to deploy. More nodes improve availability and performance.
            A good practice is to match the number of nodes to the number of configured index replicas.
            Default is a single node, which handles both ingest (writes) and queries (reads).
        opensearch_manager_node_instance_type : str, optional
            EC2 instance type for OpenSearch manager nodes, responsible for cluster coordination.
            Default is `t3.medium.search` to reduce costs for initial deployments.
            See AWS recommendations for instance types:
            https://docs.aws.amazon.com/opensearch-service/latest/developerguide/supported-instance-types.html
        opensearch_manager_node_count : int, optional
            Number of OpenSearch manager nodes to deploy. Using 3 manager nodes is recommended for production environments
            to ensure high availability and cluster stability.
        opensearch_ip_access_range : list[str], optional
            List of IP CIDR blocks on which to allow OpenSearch domain access (e.g. for security purposes).
            Default is ["127.0.0.1/32"]. 
            Note: leaving this unchanged will raise a warning no one can access your opensearch cluster.
        opensearch_volume_size : int, optional
            The size, in GB, of the OpenSearch domain's underlying EBS storage. Default is 50GB. 
        snapshot_repo_name : str, optional
            Name of the snapshot repository (used by OpenSearch to name the snapshots written to the snapshot bucket).
            Default is "opensearch-snapshot-repo".
        snapshot_lambda : Optional[lambda_.Function], optional
            Override for the default snapshot Lambda function for taking periodic snapshots of the OpenSearch cluster.
            Default is None, which creates a standard snapshot taker for you.
        snapshot_schedule : events.Schedule
            Cron schedule on which to run the snapshot Lambda. Default is 9am UTC daily.
            See https://docs.aws.amazon.com/lambda/latest/dg/tutorial-scheduled-events-schedule-expressions.html.
        """
        super().__init__(scope, construct_id)

        # User warnings
        if "127.0.0.1/32" in opensearch_ip_access_range and len(opensearch_ip_access_range) == 0:
            warnings.warn(
                "You didn't specify an IP range, now no one can access your opensearch! If "
                "this is what you intended (we think this is unlikely), you can suppress this warning."
                "To fix this, change `opensearch_ip_access_range` to a more specific CIDR block spec."
            )

        service_linked_role = CfnResource(
            self,
            "opensearch-service-linked-role",
            type="AWS::IAM::ServiceLinkedRole",
            properties={
                "AWSServiceName": "es.amazonaws.com",
                "Description": "Role for OpenSearch to access resources in the VPC",
            },
        )

        self.domain = opensearch.Domain(
            self,
            "OpenSearchDomain",
            domain_name=opensearch_domain_name,
            version=opensearch_version,
            # Define the EC2 instances/nodes
            # Supported EC2 instance types:
            # https://docs.aws.amazon.com/opensearch-service/latest/developerguide/supported-instance-types.html
            capacity=opensearch.CapacityConfig(
                # Num of nodes/instances, for prod should be 1:1 with number of shards created for the indexes
                data_nodes=int(opensearch_data_node_count),
                # m6g is 2vCPU and 8GB RAM - $100/month per data node
                data_node_instance_type=opensearch_data_node_instance_type,
                master_nodes=int(opensearch_manager_node_count),
                master_node_instance_type=opensearch_manager_node_instance_type,
            ),
            # 10GB is the minimum size
            ebs=opensearch.EbsOptions(
                volume_size=opensearch_volume_size,
                volume_type=ec2.EbsDeviceVolumeType.GP3,
            ),
            # Enable logging
            logging=opensearch.LoggingOptions(
                slow_search_log_enabled=True,
                app_log_enabled=True,
                slow_index_log_enabled=True,
            ),
            # Enable encryption
            node_to_node_encryption=True,
            encryption_at_rest=opensearch.EncryptionAtRestOptions(enabled=True),
            # Require https connections
            enforce_https=True,
            # Use our custom domain name in the endpoint
            # This will autogenerate our CNAME record
            custom_endpoint=opensearch.CustomEndpointOptions(
                domain_name=f"search.{hosted_zone.zone_name}",
                hosted_zone=hosted_zone,
                certificate=certificate,
            ),
            removal_policy=removal_policy,
            # Amazon ES will optionally distribute the nodes and shards across multiple Availability Zones
            # in the region to increase availability.
            zone_awareness=opensearch_zone_awareness,
        )

        # Add the service linked role as a dependency to the domain (i.e. role must exist first)
        self.domain.node.add_dependency(service_linked_role)

        # Define access policies and restrict to specified IPs
        self.domain.add_access_policies(
            iam.PolicyStatement(
                principals=[iam.AnyPrincipal()],
                actions=["es:*"],
                resources=[f"{self.domain.domain_arn}/*"],
                conditions={"IpAddress": {"aws:SourceIp": opensearch_ip_access_range}},
            )
        )

        # ##################################### #
        # Create resources for manual snapshots #
        # ##################################### #
        # S3 bucket to store the snapshot data
        # The data is stored in native Lucene format
        self.opensearch_snapshot_bucket = opensearch_snapshot_bucket

        # OS principal that allows the service to assume roles
        opensearch_principal = iam.PrincipalWithConditions(
            principal=iam.ServicePrincipal(
                "es.amazonaws.com",
            ),
            conditions={
                "StringEquals": {"aws:SourceAccount": environment.account},
                "ArnLike": {"aws:SourceArn": self.domain.domain_arn},
            },
        )

        # IAM role/policy for the OS domain service to assume
        self.opensearch_snapshot_role = iam.Role(
            self,
            "Role",
            role_name="opensearch_snapshot_role",
            assumed_by=opensearch_principal,
            description="Role OpenSearch assumes to write snapshots to S3 buckets",
        )

        # Policy to allow S3 access to put the new snapshots
        opensearch_snapshot_policy = iam.ManagedPolicy(
            self,
            "opensearch_snapshot_policy",
            description="CI CD automated data policy",
            managed_policy_name="opensearch_snapshot_policy",
            roles=[
                self.opensearch_snapshot_role,
            ],
            statements=[
                iam.PolicyStatement(
                    # Permission to list all S3 buckets
                    effect=iam.Effect.ALLOW,
                    actions=["s3:ListBucket"],
                    resources=[
                        f"{self.opensearch_snapshot_bucket.bucket_arn}",
                    ],
                ),
                iam.PolicyStatement(
                    effect=iam.Effect.ALLOW,
                    actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    resources=[
                        f"{self.opensearch_snapshot_bucket.bucket_arn}/*",
                    ],
                ),
            ],
        )

        if snapshot_lambda is None:
            # The lambda directory is considered a data directory for the package and is packaged along with the
            # python code during distribution.
            docker_context_path = str(
                (Path(__file__).parent.parent / "lambda_functions").absolute()
            )
            docker_image_code = lambda_.DockerImageCode.from_image_asset(
                directory=docker_context_path,
                target="snapshot-lambda",  # Hard-coded to the target name in lambda/Dockerfile
                platform=ecr_assets.Platform.LINUX_AMD64,
            )

            snapshot_lambda = lambda_.DockerImageFunction(
                self,
                "SnapshotLambda",
                code=docker_image_code,
                environment={
                    # The snapshot process requires the full API HTTP endpoint
                    "OPEN_SEARCH_ENDPOINT": f"https://{self.domain.domain_endpoint}/",
                    "SNAPSHOT_S3_BUCKET": self.opensearch_snapshot_bucket.bucket_name,
                    "SNAPSHOT_ROLE_ARN": self.opensearch_snapshot_role.role_arn,
                    "SNAPSHOT_REPO_NAME": snapshot_repo_name,
                },
                timeout=Duration.seconds(60 * 15),
                memory_size=512,
                retry_attempts=0,
            )

        # Add permissions for Lambda to access OpenSearch
        snapshot_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["es:*"],
                resources=[f"{self.domain.domain_arn}/*"],
            )
        )

        # PassRole allows services to assign AWS roles to resources and services in this account
        # The OS snapshot role is invoked within the Lambda to interact with OS, it is provided to
        # lambda via an Environmental variable in the lambda definition
        snapshot_lambda.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["iam:PassRole"],
                resources=[self.opensearch_snapshot_role.role_arn],
            )
        )

        # An Event to trigger the snapshot lambda
        snapshot_lambda_event_rule = events.Rule(
            self,
            "Rule",
            rule_name="SnapshotLambdaScheduler",
            description="Scheduler to trigger the OpenSearch Lambda snapshot function",
            schedule=snapshot_schedule,
        )
        snapshot_lambda_event_rule.add_target(targets.LambdaFunction(snapshot_lambda))


class OpenSearchIndexArchivalConstruct(Construct):
    """Construct to create method of automatically archiving indexes

    Creates a Step Function, a Lambda, and a an AWS event. The event triggers the step function,
    which runs the lambda function with different inputs to archive datasets that have grown too
    large to query. 
    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        environment: Environment,
        domain: opensearch.Domain,
        sns_alarm_topic: sns.Topic = None
    ) -> None:
        """
        Construct init.

        Parameters
        ----------
        scope : Construct
            The scope in which this Construct is instantiated, usually the `self` inside a Stack.
        construct_id : str
            ID for this construct instance, e.g. "MyOpenSearchConstruct".
        environment : Environment
            AWS environment (account and region).
        domain : opensearch.Domain
            The Opensearch instance to run the archival process on
        sns_alarm_topic : sns.Topic, optional
            The SNS topic to send archival alerts to 
        """
        super().__init__(scope, construct_id)

        if sns_alarm_topic:
            sns_alarm_topic_arn = sns_alarm_topic.topic_arn
        else:
            sns_alarm_topic_arn = ''

        # ##################################### #
        # Create resources for index archival   #
        # ##################################### #
        # INDEX SUNSET LAMBDA

        docker_context_path = str(
                (Path(__file__).parent.parent / "lambda_functions").absolute()
            )
        
        sunset_log_group = logs.LogGroup(
            self,
            "IndexSunsetLambdaLogGroup",
            retention=logs.RetentionDays.INFINITE,
            removal_policy=RemovalPolicy.RETAIN
        )

        self.sunset_lambda = lambda_.DockerImageFunction(
            self,
            "IndexSunsetLambda",
            code=lambda_.DockerImageCode.from_image_asset(
                docker_context_path,
                target="index-sunset-lambda",
                platform=ecr_assets.Platform.LINUX_AMD64,
            ),
            timeout=Duration.minutes(15), # archiving a large index usually takes around 10 minutes
            retry_attempts=0,
            log_group=sunset_log_group,
            environment={
                "INDEX_SIZE_THRESHOLD_GB": str(self.node.try_get_context("INDEX_ARCHIVAL_SIZE_THRESHOLD_GB")) or "10", 
                IngestLambdaEnv.OPEN_SEARCH_ENDPOINT: domain.domain_endpoint,
                "SNS_TOPIC_ARN": sns_alarm_topic_arn
            },
        )

        self.sunset_lambda.add_to_role_policy(
            iam.PolicyStatement(
                actions=["es:ESHttpGet", "es:ESHttpPost", "es:ESHttpPut", "es:ESHttpDelete", "es:ESHttpHead"],
                resources=[f"{domain.domain_arn}/*"]
            )
        )

        if sns_alarm_topic:
            sns_alarm_topic.grant_publish(self.sunset_lambda)
        
        ## DEFINING STEP FUNCTION

        # Step 0 - Scan OpenSearch for large indexes
        find_indexes = tasks.LambdaInvoke(
            self,
            "FindLargeIndexesStep",
            lambda_function=self.sunset_lambda,
            payload=sfn.TaskInput.from_object({"step": "find_large_indexes"}),
            result_path="$.find_indexes",
            payload_response_only=True
        )

        # Step 1 - Kick off Archival
        # This LambdaInvoke step calls the sunset Lambda with the "kickoff_archival" step.
        # It blocks writes to the original index, creates a new index, and starts the asynchronous reindexing.
        kickoff_archive = tasks.LambdaInvoke(
            self, 
            "KickoffIndexArchivalStep",
            lambda_function=self.sunset_lambda,
            payload=sfn.TaskInput.from_object({  # The payload passed to the step
                "step": "kickoff_archival",
                "index.$": "$.index"  # Extract index from current Map iteration item using JSONPath
            }),
            result_path="$.kickoff",  # Save the Lambda's output in the state machine execution context
            payload_response_only=True
        )

        # Step 2 - Poll Reindex task
        # This LambdaInvoke step checks the status of the reindex task started in kickoff_archival.
        # It returns either "COMPLETED" or "IN_PROGRESS" depending on the task status.
        poll_reindex = tasks.LambdaInvoke(
            self,
            "PollReindexTaskStep",
            lambda_function=self.sunset_lambda,
            payload=sfn.TaskInput.from_object({
                "step": "poll_reindex_task",
                "index.$": "$.kickoff.index",
                "new_index.$": "$.kickoff.new_index",
                "task_id.$": "$.kickoff.task_id"
            }),
            result_path="$.kickoff",
            payload_response_only=True
        )

        # Wait state between polls (2.5 minutes)
        # Introduces a delay (2.5 minutes) between polling attempts to avoid overloading the cluster
        wait = sfn.Wait(
            self, "WaitForReindexStep",
            time=sfn.WaitTime.duration(Duration.minutes(2.5))
        )

        # Step 3 - archival cleanup
        # After reindexing completes, this step adds replicas to the new index and deletes the original index.
        cleanup_archive = tasks.LambdaInvoke(
            self, 
            "Cleanup Archival",
            lambda_function=self.sunset_lambda,
            payload=sfn.TaskInput.from_object({
                "step": "cleanup_archival",
                "index.$": "$.kickoff.index",
                "new_index.$": "$.kickoff.new_index"
            }),
            result_path="$.cleanup",
            payload_response_only=True
        )

        # Choice: if reindex still in progress, loop back
        # This is similar to an "if/else" block for Step Functions.
        # If reindexing is still in progress, loop back to wait -> poll_reindex.
        poll_choice = sfn.Choice(self, "Reindex Completed?")
        
        poll_choice.when(
            sfn.Condition.string_equals("$.kickoff.status", "IN_PROGRESS"),
            wait.next(poll_reindex).next(poll_choice) # Loop back to the wait step
        )
        poll_choice.when(
            sfn.Condition.string_equals("$.kickoff.status", "COMPLETED"),
            cleanup_archive # Move to cleanup if reindex finished
        )
        poll_choice.otherwise(
            sfn.Fail(self, "Reindex Failed",
                     cause="Reindexing did not complete",
                     error="TaskFailed")
        )

        # Chain states together 
        # The order here sets the default execution path of the state machine
        per_index_flow = kickoff_archive.next(poll_choice)
        archive_map = sfn.Map(
            self,
            "ArchiveEachIndex",
            items_path="$.find_indexes", # Access current array item from Map iteration
            item_selector={"index.$": "$$.Map.Item.Value"},
            result_path="$.archived_indexes"
        )
        archive_map.item_processor(per_index_flow)
        archive_def = find_indexes.next(archive_map)


        # Create the state machine
        self.archive_state_machine = sfn.StateMachine(
            self, "IndexSunsetStateMachine",
            definition_body=DefinitionBody.from_chainable(archive_def),
            timeout=Duration.hours(12),  # plenty of time for large indexes
            logs=sfn.LogOptions(
                destination=logs.LogGroup(
                    self,
                    "IndexSunsetStateMachineLog",
                    retention=logs.RetentionDays.INFINITE,
                    removal_policy=RemovalPolicy.RETAIN
                ),
                level=sfn.LogLevel.ALL
            )
        )

        # Set up an event bridge rule to trigger the statemachine once every 24 hours
        daily_trigger = events.Rule(
            self,
            "DailyIndexSunsetSchedule",
            schedule=events.Schedule.rate(Duration.hours(24)),
            description="Triggers the IndexSunset state machine daily to archive large indexes."
        )

        daily_trigger.add_target(
            targets.SfnStateMachine(
                self.archive_state_machine,
            )
        )
        self.archive_state_machine.grant_start_execution(iam.ServicePrincipal("events.amazonaws.com"))