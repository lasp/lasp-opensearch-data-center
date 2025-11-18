# Ingest Processing Construct Usage

The Ingest Processing Construct contains resources uses to orchestrate ingestion of data files into OpenSearch. 
The actual processing code must be supplied in the form of Lambda functions: Dropbox Lambda and Ingest Lambda.

## Components

- Orchestration Queues that pass S3 creation events from the Dropbox Bucket and Ingest Bucket to their respective
  Lambda functions. These functions are provided with a standard set of environment variables (see `constants.py`).
- Ingest Status Table which can be used to keep track of ingest status, using the functions provided in 
  `ingest_status.py` for interacting with DynamoDB (or you can write your own DDB code in your Lambda).
- Optional backup plan for Ingest Status Table (if backup_vault is provided)

## Configuring Lambda Environment Variables

The Dropbox and Ingest Lambda functions must be externally defined by the user of this library and passed in to the 
`IngestProcessingConstruct` as configuration objects. Internally, the construct configures the Lambdas with the correct 
environment variables, standardized in `constants.py` using the enums `DropboxLambdaEnv` and `IngestLambdaEnv`. 
However, this configuration is only an update to the Lambda 
environment. You as the user can pass whatever environment variables you want associated with the Lambda and the 
`IngestProcessingConstruct` will not overwrite any pre-defined environment variables. It will only add missing 
variables.

# Example Usage in Stack

```python
"""Example Stack for OpenSearch deployment using our OpenSearchConstruct"""
from pathlib import Path
from constructs import Construct
from aws_cdk import (
    Duration,
    Environment,
    Stack,
    aws_s3 as s3,
    aws_opensearchservice as opensearch,
    aws_lambda as lambda_,
    aws_ecr_assets as ecr_assets
)
from lasp_opensearch_data_center.constructs.ingest_orchestration import IngestProcessingConstruct


class IngestStack(Stack):
    """Example Stack for creating ingest orchestration and processing resources"""
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        environment: Environment,
        open_search_domain: opensearch.Domain,
        dropbox_bucket: s3.Bucket,
        ingest_bucket: s3.Bucket,
        **kwargs
    ):
        super().__init__(scope, construct_id, env=environment, **kwargs)
        docker_context_path = str((Path(__file__).parent / "lambda_code_directory_containing_a_Dockerfile").absolute())

        # Create your processing Lambda Functions: Dropbox Lambda and Ingest Lambda
        # This allows you to customize how these Lambdas validate and process your data. 
        # When they are passed to the IngestProcessingConstruct, they are provided a standard set of environment 
        # variables, so they can access OpenSearch and know the ingest and dropbox bucket names
        # Environment variable standard names are available in `lasp_opensearch_data_center.constants`
        self.dropbox_lambda = lambda_.DockerImageFunction(
            self,
            "DropboxLambda",
            code=lambda_.DockerImageCode.from_image_asset(
                directory=docker_context_path,
                target="dropbox-lambda",  # target name in Dockerfile
                platform=ecr_assets.Platform.LINUX_AMD64
            )
        )
        
        self.ingest_lambda = lambda_.DockerImageFunction(
            self,
            "IngestLambda",
            code=lambda_.DockerImageCode.from_image_asset(
                directory=docker_context_path,
                target="ingest-lambda",  # target name in Dockerfile
                platform=ecr_assets.Platform.LINUX_AMD64
            ),
            timeout=Duration.seconds(60 * 15)
        )
        
        self.ingest_construct = IngestProcessingConstruct(
            self,
            "IngestOrchestration",
            open_search_domain=open_search_domain,
            dropbox_bucket=dropbox_bucket,
            ingest_bucket=ingest_bucket,
            dropbox_lambda=self.dropbox_lambda,
            ingest_lambda=self.ingest_lambda
        )
```
