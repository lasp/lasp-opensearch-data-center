# Frontend Construct Usage

This construct creates resources used to host a front end website on top of the data center back end.

## Components

- Policies to allow users in a deployment IAM group to deploy static resources to the front end storage S3 bucket
- Hosted zone with specified domain name
- Cloudfront certificate for SSL
- IP range restriction for access to the website
- Cloudfront distribution for serving the static site

# Example Usage in Stack

```python
"""Example front end website stack"""
from aws_cdk import (
    Stack,
    aws_s3 as s3,
    Environment,
)
from constructs import Construct
from lasp_opensearch_data_center.frontend import FrontendConstruct


class FrontEndStack(Stack):
    """Example Front End website Stack"""

    def __init__(
            self,
            scope: Construct,
            construct_id: str,
            account_type: str,
            domain_name: str,
            frontend_bucket: s3.Bucket,
            environment: Environment,
            **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, env=environment, **kwargs)

        self.frontend = FrontendConstruct(
            self,
            construct_id=construct_id,
            environment=environment,
            account_type=account_type,
            domain_name=domain_name,
            frontend_bucket=frontend_bucket,
            waf_ip_range="10.1.1.1/16"  # Custom IP range
        )
```
