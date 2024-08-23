# LASP OpenSearch Data Center CDK Constructs

A construct library for implementing an OpenSearch data center. This library contains the following constructs:
* BackendStorageConstruct
* BackupVault
* Certificate
* FrontendStorageConstruct
* NetworkingComponents
* Frontend
* OpenSearch
* CloudWatchAlarmConstruct
* Ingest
* DynamoQuery

Example usage: 

```
domain_name = "example.com"
self.frontendStorage = FrontendStorage(
    self, domain_name, construct_id, environment, **kwargs
)

account_type = "dev"
self.backendStorage = BackendStorage(
    self,
    construct_id,
    dropbox_bucket_name=f"{account_type}-example-dropbox",
    ingest_bucket_name=f"{account_type}-example-ingest",
    opensearch_snapshot_bucket_name=f"{account_type}-example-opensearch-manual-snapshot",
)

frontend_bucket = self.frontendStorage.frontend_bucket
self.frontend = FrontEndConstruct(
    self,
    construct_id=construct_id,
    account_type=account_type,
    domain_name=domain_name,
    frontend_bucket=frontend_bucket,
    waf_ip_range="1.1.1.0/24",  # Example IP range
    environment=environment,
)

self.networking = NetworkingComponentsConstruct(self, construct_id)

self.certificate = CertificateConstruct(
    self, "CertificateConstruct", domain_name
)

# TODO: add example usage for constructs as they are completed
```

