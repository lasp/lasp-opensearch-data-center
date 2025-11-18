# Networking Construct Usage

This is here to provide a working default VPC for running a data center. This VPC should be used across all 
data center stacks and constructs so everything is in the same network.

## Components

- VPC with 1 isolated and 1 public subnet
- No NAT gateways in the VPC because they are not needed

# Example Usage in Stack

```python
"""Shared networking resources"""
from constructs import Construct
from aws_cdk import (
    Environment,
    Stack
)
from lasp_opensearch_data_center.constructs.networking import NetworkingComponentsConstruct


class NetworkingComponents(Stack):
    """General networking resources for a Data Center"""

    def __init__(
            self, scope: Construct, construct_id: str, environment: Environment, **kwargs
    ) -> None:
        super().__init__(scope, construct_id, env=environment, **kwargs)

        self.networking = NetworkingComponentsConstruct(self, construct_id)
        self.vpc = self.networking.vpc
```
