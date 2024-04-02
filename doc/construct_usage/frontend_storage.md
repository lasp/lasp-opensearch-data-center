# FrontendStorage Construct Usage

The front end storage construct creates the storage resources for a website for the data center. The idea 
is that a separate web development team will be creating the static website on top of the OpenSearch API and 
periodically deploying it to the website S3 bucket. The tag policy prevents anyone else from messing with the
website contents. See the FrontendConstruct for the IAM policy and group that gets access to this bucket.

## Components

- S3 bucket for storing website contents
- Tag-based policy to prevent creation and deletion of website contents from S3 bucket

# Example Usage in Stack

```python
"""Example front end storage stack"""
from aws_cdk import (
    Stack,
    Environment
)
from constructs import Construct
from lasp_opensearch_data_center.frontend_storage import FrontendStorageConstruct


class FrontendStorageStack(Stack):
    """Example FrontendStorageStack"""

    def __init__(
            self,
            scope: Construct,
            domain_name: str,  # Pass this in
            construct_id: str,
            environment: Environment,
            **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, env=environment, **kwargs)

        self.frontendStorage = FrontendStorageConstruct(
            self, construct_id, environment, domain_name=domain_name
        )

        self.frontend_bucket = self.frontendStorage.frontend_bucket
```