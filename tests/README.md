# Unit tests

These tests synthesize each construct into a CloudFormation template, with `aws_cdk.assertions`, and check the
resources in it. They make no AWS calls, since synthesizing never does: the CDK CLI makes the lookups, and the tests
answer them from context instead (`hosted_zone_context()` in `construct_helpers.py` for hosted zones, and the
availability zones in `test_networking_construct.py`). Every test stack uses the made-up account `123456789012`.

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

| File | Construct | What the tests check |
|------|-----------|----------------------|
| `test_backend_storage_construct.py` | `BackendStorageConstruct` | Bucket names, removal and versioning; queue visibility timeouts, retention and dead-letter settings; which bucket prefixes notify which queue; the upload role's trust and its exact S3 permissions |
| `test_certificate_construct.py` | `CertificateConstruct` | The wildcard certificate, its DNS validation, and the exported certificate ARN |
| `test_frontend_construct.py` | `FrontendConstruct` | The us-east-1 requirement; the guest identity pool and role; the deploy user, role and exact deploy policy; the certificate, WAF IP set and rules; the CloudFront distribution, its default root object, its bucket policy and the DNS alias |
| `test_frontend_storage_construct.py` | `FrontendStorageConstruct` | The site bucket's name, ACL setting and removal, and the us-east-1 requirement |
| `test_ingest_orchestration_construct.py` | `IngestProcessingConstruct` | The ingest status table and its backup; each Lambda's environment, including string keys and a variable named twice; each Lambda's queue and exact permissions |
| `test_networking_construct.py` | `NetworkingComponentsConstruct` | The VPC's CIDR, subnets, routes and the absence of NAT gateways |
| `test_opensearch_construct.py` | `OpenSearchConstruct` | The domain's nodes, version, storage, encryption, endpoint, TLS policy, logs, removal and access policy, the refusal of an access range that is not a list of addresses or CIDR blocks, and the warning for the default one; the snapshot role; the snapshot Lambda's environment, image, limits, permissions and schedule |

A test of an argument passes a value other than its default and finds it in the template, so a construct that drops
the argument fails it. An IAM test (`role_policy_statements()` in `construct_helpers.py`) gathers a role's statements
from its inline policies and from every `AWS::IAM::Policy` and `AWS::IAM::ManagedPolicy` that names it, and compares
them with the full list expected, so a grant added through another policy fails it too. The statements of a policy AWS
manages are not in the template, so the test requires the role to have exactly the expected ones of those.

## What they do not cover

- Deploying: a template can synthesize and still be refused by CloudFormation or AWS.
- The Lambda code that runs in a consumer's account, apart from building the snapshot Lambda's image.
