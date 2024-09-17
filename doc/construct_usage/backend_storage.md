# Back End Storage Construct Usage

This construct provides sensible basic set of storage buckets to provide persistent storage for the back end of the 
data center, including arrival notification and queueing of those events for later processing. 
If you wish, you can create these resources independently and pass them to the subsequent constructs that need to 
know about them. 
These resources are suggested to be deployed in their own stack so that the rest of the data center can be destroyed 
without affecting long term data storage. For example, if violated this suggestion and 
you create the OpenSearch snapshot bucket in the same stack 
as the OpenSearch Domain, if you ever tear down the domain, the bucket will also be destroyed (or its removal policy
will prevent destruction of the Stack itself, leaving it in a failed state).

## Components

- Dropbox bucket for handling new files (dropbox pre-processor reads from here)
- Ingest bucket for storing valid new files (ingest processor reads from here)
- Opensearch snapshot storage bucket (Opensearch saves index snapshots here)

# Example Usage in Stack

```python
"""Example Stack for deploying BackendStorage Construct"""
from constructs import Construct
from aws_cdk import (
    Environment,
    Stack
)
from lasp_opensearch_data_center.backend_storage import BackendStorageConstruct


class BackendStorageStack(Stack):
    """Stack containing resources for persistent back end storage"""

    def __init__(
            self,
            scope: Construct,
            construct_id: str,
            environment: Environment,
            **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, env=environment, **kwargs)

        self.backendStorage = BackendStorageConstruct(
            self,
            "BackendStorageConstruct",
            dropbox_bucket_name="dropbox-bucket",
            ingest_bucket_name="ingest-bucket",
            opensearch_snapshot_bucket_name="opensearch-snapshot-bucket",
        )
```
