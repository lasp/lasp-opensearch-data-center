# LASP OpenSearch Data Center CDK Constructs

*This project is a work in progress and is not ready for primetime.*

This is a Python package containing AWS CDK constructs for building an engineering data center based on OpenSearch.

## Front End

### Front End Stack
L3 Construct that supports deployment of a static website hosted from an S3 bucket, including IAM policy 
permissions that allow deployment of static files by users within a construct-defined IAM group.

### Front End Storage Stack
Storage buckets for the front end website.

## Back End

### Networking Construct
L3 Construct containing a custom VPC with specific subnet configurations.

### Back End Storage Construct
L3 Construct containing the necessary storage infrastructure that supports the back end ingest pipeline and 
Opensearch.

### Ingest Construct
Not yet implemented as a L3 Construct

# Installation

The package is available on PyPI:

```shell
pip install lasp-opensearch-data-center
```

# Roadmap and Plans

- Incorporate the last L3 construct for OpenSearch and data ingest
- Add static code analysis and automated security testing (e.g. Bandit) using Github actions
- Write user documentation for putting these lego blocks together, including example code for Stacks (e.g. an example CDK app)
    - Will include detailed instructions on customizing ingest handler code
- Clean up the docstrings and improve overall code polish
- Write developer documentation for developing further
