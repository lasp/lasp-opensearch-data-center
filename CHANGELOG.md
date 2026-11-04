# Version Changes
## v1.1.0 (released)
- Added a `tls_security_policy` parameter to `OpenSearchConstruct`, defaulting to `TLS_1_2_PFS` (AWS's most secure TLS policy).
- Requires Python 3.13 or later (was 3.9), and `aws-cdk-lib` 2.178.0 or later: `IngestProcessingConstruct` uses
  `dynamodb.PointInTimeRecoverySpecification`, first in 2.178.0, and `OpenSearchConstruct` uses
  `TLSSecurityPolicy.TLS_1_2_PFS`, first in 2.119.0. The snapshot Lambda's image now uses Python 3.13, and its unused
  `cloudpathlib` dependency is removed.
- Fixed OpenSearch snapshots being corrupted by the snapshot bucket's 90-day S3 expiration rule. Snapshots are
  incremental, so expiring objects by age deleted files that current snapshots still depended on. Since v1.0.8
  turned bucket versioning off by default, those deletions were permanent. The rule is removed.
- The snapshot Lambda now enforces retention through the OpenSearch snapshot API, which only deletes files no
  remaining snapshot references. Configure with the new `snapshot_retention_days` argument to `OpenSearchConstruct`
  (default 90, `None` keeps all snapshots; with the default Lambda, a whole number from 1 to 36500). Only snapshots named in the
  Lambda's own format (`os_snapshot_YYYY-MM-DD-HH:MM:SS`) are pruned, the newest successful one is always kept,
  and pruning runs after the day's snapshot has been requested. The listing does not read per-snapshot metadata,
  so repositories where the S3 expiry has already removed it do not block pruning.
- A deletion that has not finished within a minute is left running on the cluster, and the next run selects again
  whatever it did not delete. The run fails, after taking its snapshot, when a deletion that was not confirmed
  includes a snapshot more than 7 days past the retention window. That shows deletions that never finish, and it also
  happens for a few days when pruning starts on older snapshots: on upgrading a repository that has some, after
  lowering `snapshot_retention_days`, or while more than 20 expired snapshots remain. Alarm on the snapshot Lambda's
  `Errors` metric to hear of it.
- Added unit tests for the snapshot bucket configuration and the retention logic.
- Added unit tests for every construct, and a `Unit tests` workflow that runs them, and builds the snapshot Lambda's
  image, on every pull request.
- `IngestProcessingConstruct` grants its Lambdas only the S3 actions moving and reading files needs, instead of
  `s3:*` on both buckets' objects (and `s3:ListObjects`, which is not an IAM action). The Dropbox Lambda may read,
  write and delete the Dropbox Bucket's files and write the Ingest Bucket's; the Ingest Lambda may only read the
  Ingest Bucket's. A Lambda that needs more must be granted it by the caller.
- `FrontendConstruct`'s deploy role may only list the site bucket and upload files under `frontend/` (and cancel an
  upload); a deploy that deletes, reads, copies or tags files must be granted more. It had `s3:*` on the bucket
  itself, including deleting it and changing its policy, and could list every bucket's name and location.
- `dropbox_lambda_env` and `ingest_lambda_env` accept variable names as strings, beside the enum members. A string
  key used to fail synth. Naming one variable twice in the same dictionary raises `ValueError`.
- The CloudFront distribution's default root object is `index.html`. It was `/index.html`, and AWS says the name
  must not begin with a slash, which can lead to a 403.
- `OpenSearchConstruct` now warns when `opensearch_ip_access_range` is left at its default, as its docstring said.
- `OpenSearchConstruct` raises `ValueError` unless `opensearch_ip_access_range` is a non-empty list of IP addresses
  or CIDR blocks, in CIDR notation. Before, an empty list left the access policy's `aws:SourceIp` empty, None
  failed synth with a `TypeError`, and a single string, which the usage docs showed, was accepted.
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
