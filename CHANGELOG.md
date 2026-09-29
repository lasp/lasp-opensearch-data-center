# Version Changes
## v1.0.9 (unreleased)
- Fixed OpenSearch snapshots being corrupted by the snapshot bucket's 90-day S3 expiration rule. Snapshots are
  incremental, so expiring objects by age deleted files that current snapshots still depended on. Since v1.0.8
  turned bucket versioning off by default, those deletions were permanent. The rule is removed.
- The snapshot Lambda now enforces retention through the OpenSearch snapshot API, which only deletes files no
  remaining snapshot references. Configure with the new `snapshot_retention_days` argument to `OpenSearchConstruct`
  (default 90, `None` keeps all snapshots).
- Added unit tests for the snapshot bucket configuration and the retention logic.

## v1.0.8 (released)
- Updated the max file size check for incoming files to the Dropbox Lambda and set limit to 30MB.
- Made S3 bucket versioning a configurable parameter.
- Updated OpenSearch to support inbound IP list with a restrictive localhost default.
- Added documentation.

## v1.0.7 (released)
- Allow for configuration of the OpenSearch instance EBS size in the construct

## v1.0.6 (released)
- Changed Dropbox to only send out notifications to the SQS for files in the "received-fles" folder. 

## v1.0.5 (released)
- Added dedicated manager node(s) to Opensearch
- Removed deprecated DynamoDB CDK construct parameter

## v1.0.4 (released)
- Added IAM role and policy for s3 dropbox ingests

## v1.0.3 (released)
- Added IAM user, role, policy for frontend deployment user

## v1.0.2 (released)
- Added cognito group for website access
- Added release documentation

## v1.0.1 (released)
- Refactor lambda runtime subpackage into proper python package

## v1.0.0 (released)
- `IngestProcessingConstruct` for deploying the orchestration and Lambdas that run ingest processing
- `OpenSearchConstruct` for deploying OpenSearch cluster with built-in snapshot Lambda function
- `CertificateConstruct` that creates SSL certs for an existing Hosted Zone
- `NetworkingComponentConstruct` for VPC and subnet infrastructure
- `BackendStorage` construct for storage of back end data
- `FrontEndConstruct` for deploying a website for the data center 
- `FrontendStorage` construct for storage of static website
- Added license, code of conduct, and more detail to readme
