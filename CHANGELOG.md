# Version Changes

## v1.0.5 (released)
- Add dedicated manager node to Opensearch
- Removed depracated DynamoDB CDK construct parameter

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
