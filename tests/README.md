# Unit tests

Most of these tests synthesize each construct into a CloudFormation template, with `aws_cdk.assertions`, and check
the resources in it; `test_snapshot_handler.py` runs the snapshot Lambda's handler with its HTTP requests mocked.
None of them calls AWS. Synthesizing never does: the CDK CLI makes the lookups, and the tests answer them from
context instead (`hosted_zone_context()` in `construct_helpers.py` for hosted zones, and the availability zones in
`test_networking_construct.py`). The handler tests mock `requests` and give the handler made-up credentials. Every
test stack uses the made-up account `123456789012`.

## Running them

```bash
poetry install                  # The library and its dev dependencies, including pytest
poetry run pytest tests/        # Every test
poetry run pytest tests/test_frontend_construct.py   # One file
```

The **Unit tests** workflow (`.github/workflows/unit.yml`) runs them on every pull request and every push to `main`,
and also builds the snapshot Lambda's Docker image. To run it without a pull request, open **Actions → Unit tests →
Run workflow** and pick a branch. GitHub offers that button only once the workflow file is on `main`.

## What each file covers

| File | Code under test | What the tests check |
|------|-----------|----------------------|
| `test_backend_storage_construct.py` | `BackendStorageConstruct` | Bucket names, removal and versioning; queue visibility timeouts, retention and dead-letter settings; which bucket prefixes notify which queue; the upload role's trust and its exact S3 permissions |
| `test_certificate_construct.py` | `CertificateConstruct` | The wildcard certificate, its DNS validation, and the exported certificate ARN |
| `test_frontend_construct.py` | `FrontendConstruct` | The us-east-1 requirement; the guest identity pool and role; the deploy user, role and exact deploy policy; the certificate, WAF IP set and rules; the CloudFront distribution, its default root object, its bucket policy and the DNS alias |
| `test_frontend_storage_construct.py` | `FrontendStorageConstruct` | The site bucket's name, ACL setting and removal, and the us-east-1 requirement |
| `test_ingest_orchestration_construct.py` | `IngestProcessingConstruct` | The ingest status table and its backup; each Lambda's environment, including string keys and a variable named twice; each Lambda's queue and exact permissions |
| `test_networking_construct.py` | `NetworkingComponentsConstruct` | The VPC's CIDR, subnets, routes and the absence of NAT gateways |
| `test_opensearch_construct.py` | `OpenSearchConstruct` | The domain's nodes, version, storage, encryption, endpoint, TLS policy, logs, removal and access policy, the refusal of an access range that is not a list of addresses or CIDR blocks, and the warning for the default one; the snapshot role; the snapshot Lambda's environment, image, limits, permissions and schedule |
| `test_snapshot_handler.py` | The snapshot Lambda's handler | Which snapshots pruning selects (name format, newest success kept, states, cutoff, at most 20, future dates); the listing and delete requests; how a read or connect timeout, a 504, a 404 and a 500 are handled, and when an overdue snapshot fails the run; that the snapshot is requested first, that pruning is skipped when the snapshot fails or retention is unset, and that a pruning failure or a retention below 1 is raised only after the snapshot; that the configured retention is used |
| `test_snapshot_retention_cdk.py` | `BackendStorageConstruct`, `OpenSearchConstruct` | The snapshot bucket has no lifecycle rule; `snapshot_retention_days` reaches the Lambda, with its default and `None`, and anything but a whole number from 1 to 36500 (a float, `True`, `"90"`) is refused |

A test of an argument passes a value other than its default and finds it in the template, so a construct that drops
the argument fails it. An IAM test (`role_policy_statements()` in `construct_helpers.py`) gathers a role's statements
from its inline policies and from every `AWS::IAM::Policy` and `AWS::IAM::ManagedPolicy` that names it, and compares
them with the full list expected, so a grant added through another policy fails it too. The statements of a policy AWS
manages are not in the template, so the test requires the role to have exactly the expected ones of those.

## What they do not cover

- Deploying: a template can synthesize and still be refused by CloudFormation or AWS.
- The Lambda code that runs in a consumer's account, apart from the snapshot handler and building its image. The
  handler has not run against a real OpenSearch domain.
