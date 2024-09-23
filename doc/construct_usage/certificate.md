# Certificate Construct Usage

This construct deploys a generic certificate for use throughout a data center back end. 

NOTE: This requires you to have a Route53 Hosted Zone with your registered domain name in the AWS console.

## Components

- SSL certificates used across the back end of the data center
- Configuration output for cross-stack reference

# Example Usage in Stack

```python
"""Example Certificate Stack"""
from aws_cdk import (
    Stack,
    Environment,
)
from constructs import Construct
from lasp_opensearch_data_center.constructs.certificate import CertificateConstruct


class CertificateStack(Stack):
    """Manage certificates needed for the Data Center application."""
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        domain_name: str,
        environment: Environment,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, env=environment, **kwargs)

        certificate = CertificateConstruct(
            self,
            "CertificateConstruct",
            domain_name=domain_name
        )

        self.hosted_zone = certificate.hosted_zone
        self.account_cert = certificate.account_cert
```