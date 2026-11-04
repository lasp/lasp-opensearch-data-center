# Version Changes
## v1.0.9 (released)
- Added a `tls_security_policy` parameter to `OpenSearchConstruct`, defaulting to `TLS_1_2_PFS` (AWS's most secure TLS policy).
- Added unit tests for every construct, and a `Unit tests` workflow that runs them, and builds the snapshot Lambda's
  image, on every pull request.
- `IngestProcessingConstruct` grants its Lambdas only the S3 actions moving and reading files needs, instead of
  `s3:*` on both buckets' objects (and `s3:ListObjects`, which is not an IAM action). The Dropbox Lambda may read,
  write and delete the Dropbox Bucket's files and write the Ingest Bucket's; the Ingest Lambda may only read the
  Ingest Bucket's. A Lambda that needs more must be granted it by the caller.
- `FrontendConstruct`'s deploy role may list the site bucket and read, upload, tag, replace and delete files under
  `frontend/`. It had `s3:*` on the bucket itself, including deleting it and changing its policy, and could list every
  bucket's name and location.
- `dropbox_lambda_env` and `ingest_lambda_env` accept variable names as strings, beside the enum members. A string
  key used to fail synth. Naming one variable twice in the same dictionary raises `ValueError`.
- The CloudFront distribution's default root object is `index.html`. It was `/index.html`, and AWS says the name
  must not begin with a slash, which can lead to a 403.
- `OpenSearchConstruct` now warns when `opensearch_ip_access_range` is left at its default, as its docstring said.
- `OpenSearchConstruct` raises `ValueError` unless `opensearch_ip_access_range` is a non-empty list of IP addresses
  or CIDR blocks, in CIDR notation. None used to give the domain's access policy an empty condition, and a single string, which the
  usage docs showed, is no longer accepted.
- Corrected docstrings: `account_type` on `FrontendConstruct` is unused, `opensearch_version` is an `EngineVersion`.

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
