# OpenSearch Construct Usage

The OpenSearch Construct contains all the architecture required for deploying an OpenSearch cluster. It requires 
some configuration as input (construct arguments) for elements of the architecture that are judged to be 
the responsibility of the user implementing a CDK application (e.g. a data center).

## Components

- OpenSearch Domain (cluster and nodes), with attached access policy for configured IP range
- Lambda function for taking snapshots of opensearch indexes, scheduled to run daily

# Example Usage in Stack

```python
"""Example Stack for OpenSearch deployment using our OpenSearchConstruct"""
from constructs import Construct
from aws_cdk import (
    Environment,
    Stack,
    RemovalPolicy,
    aws_route53 as route53,
    aws_certificatemanager as acm,
    aws_s3 as s3,
    aws_opensearchservice as opensearch
)
from lasp_opensearch_data_center.constructs.opensearch import OpenSearchConstruct


class OpenSearch(Stack):
    """OpenSearch stack to create the Open Search Domain and cluster nodes

    NOTE: This stack takes ~20-40 minutes to deploy/destroy the OpenSearch service.
    Access to the website GUI is available via https://search.{domain_name}/_dashboards/app/home#/
    """

    def __init__(
            self,
            scope: Construct,
            construct_id: str,
            hosted_zone: route53.HostedZone,
            certificate: acm.Certificate,
            environment: Environment,
            snapshot_bucket: s3.Bucket,
            **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, env=environment, **kwargs)
        self.opensearch = OpenSearchConstruct(
            self,
            "OpensearchConstruct",
            hosted_zone=hosted_zone,  # Controls URL of OpenSearch API/Dashboards
            certificate=certificate,
            environment=environment,
            opensearch_snapshot_bucket=snapshot_bucket,
            opensearch_version=opensearch.EngineVersion.open_search("2.5"),  # Replace with desired version
            opensearch_zone_awareness=None,
            opensearch_data_node_instance_type="t3.medium.search",  # Always use *.search instances for opensearch
            opensearch_data_node_count=1,
            opensearch_manager_node_instance_type="t3.medium.search",
            opensearch_manager_node_count=1
            opensearch_domain_name="opensearch-testing",  # Name of domain, shows up in console
            opensearch_ip_access_range="10.1.1.1/16",  # Replace with custom IP range to control OpenSearch access
            removal_policy=RemovalPolicy.DESTROY  # Modify this as needed for data persistence requirements
        )
```