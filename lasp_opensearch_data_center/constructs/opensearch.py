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
    aws_ecr_assets as ecr_assets
)
# Local
from lasp_opensearch_data_center.constructs.constants import OPENSEARCH_SNAPSHOT_REPO_NAME


class OpenSearchConstruct(Construct):
    """OpenSearch Construct to create the Open Search Domain and cluster nodes

    NOTE: This construct takes ~20-40 minutes to deploy/destroy the OpenSearch service.
    Access to the website GUI is available via https://search.{hosted_zone.zone_name}/_dashboards/app/home#/
    """
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        environment: Environment,
        hosted_zone: route53.HostedZone,
        certificate: acm.Certificate,
        opensearch_snapshot_bucket: s3.Bucket,
        opensearch_domain_name: str,
        opensearch_instance_type: str = "t3.medium.search",
        opensearch_version: opensearch.EngineVersion = opensearch.EngineVersion.open_search("2.9"),
        opensearch_zone_awareness: Optional[opensearch.ZoneAwarenessConfig] = None,
        opensearch_node_count: int = 1,
        opensearch_ip_access_range: str = '0.0.0.0/0',
        snapshot_repo_name: str = OPENSEARCH_SNAPSHOT_REPO_NAME,
        removal_policy: RemovalPolicy = RemovalPolicy.RETAIN,
        snapshot_lambda: Optional[lambda_.Function] = None,
        snapshot_schedule: events.Schedule = events.Schedule.cron(
                minute="0", hour="9", month="*", week_day="*", year="*"
            )
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
        opensearch_instance_type : str, optional
            EC2 instance type on which to run OpenSearch (needs significant resources).
            Default is `t3.medium.search` to keep costs down on your first deployment.
            AWS lists supported OpenSearch instance types and recommendations here:
            https://docs.aws.amazon.com/opensearch-service/latest/developerguide/supported-instance-types.html
        opensearch_version : str, optional
            Version of OpenSearch to deploy, e.g. "2.5". Default is "2.9".
        opensearch_zone_awareness : opensearch.ZoneAwarenessConfig, optional
            AWS can optionally distribute OpenSearch nodes across multiple AZs to increase availability.
            Default is None (no zone awareness).
        opensearch_node_count : int, optional
            Number of OpenSearch nodes to deploy. If availability becomes an issue, increasing the number of nodes can help.
            A rule of thumb is to keep the number of nodes 1:1 with the number of shards configured in your indexes.
            Default is a single node, used for ingest (write) and read queries.
        opensearch_ip_access_range : str, optional
            IP CIDR block on which to allow OpenSearch domain access (e.g. for security purposes).
            Default is 0.0.0.0/0 (open everywhere). Note: leaving this unchanged will raise a warning that your cluster
            is available to the public internet.
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
        if opensearch_ip_access_range == "0.0.0.0/0":
            warnings.warn("You are creating an OpenSearch cluster that is available to the public internet. If "
                          "this is what you intended (we think this is unlikely), you can suppress this warning."
                          "To fix this, change `opensearch_ip_access_range` to a more specific CIDR block spec.")

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
                data_nodes=int(opensearch_node_count),
                # m6g is 2vCPU and 8GB RAM - $100/month per data node
                data_node_instance_type=opensearch_instance_type,
            ),
            # 10GB is the minimum size
            ebs=opensearch.EbsOptions(
                volume_size=50,
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
                certificate=certificate
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
            docker_context_path = str((Path(__file__).parent.parent / "lambda").absolute())
            docker_image_code = lambda_.DockerImageCode.from_image_asset(
                directory=docker_context_path,
                target="snapshot-lambda",  # Hard-coded to the target name in lambda/Dockerfile
                platform=ecr_assets.Platform.LINUX_AMD64
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
            schedule=snapshot_schedule
        )
        snapshot_lambda_event_rule.add_target(targets.LambdaFunction(snapshot_lambda))
