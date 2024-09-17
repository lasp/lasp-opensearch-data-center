# Certificate Construct Usage

This construct deploys a generic certificate for use throughout a data center back end. 

NOTE: This requires you to have a Route53 Hosted Zone with your registered domain name in the AWS console.

## Components

- SSL certificates used across the back end of the data center
- Configuration output for cross-stack reference

# Example Usage in Stack

```python
"""Example Certificate Stack"""
from aws_cdk import (
    Stack,
    aws_certificatemanager as acm,
    CfnOutput,
    Environment,
    aws_route53 as route53,
)
from constructs import Construct


class CertificateStack(Stack):
    """Manage certificates needed for the Data Center application."""
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        domain_name: str,
        environment: Environment,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, env=environment, **kwargs)

        # Import existing Hosted Zone. You can alternatively _create_ a Hosted Zone here if you don't already have one.
        self.hosted_zone = route53.HostedZone.from_lookup(
            self,
            "HostedZone",
            domain_name=domain_name,  # e.g. [dev|prod].my-domain.net
        )

        # Create a single multi-use cert for this account: *.domain_name
        # in the default region set by the app using os.environ["CDK_DEPLOY_REGION"]
        self.account_cert = acm.Certificate(
            self,
            "DataCenterCertificate",
            domain_name=f"*.{self.hosted_zone.zone_name}",
            validation=acm.CertificateValidation.from_dns(hosted_zone=self.hosted_zone),
        )

        # This allows us to do cross-stack references and use this certificate
        # in other apps
        # Here is an example of how to import it from another stack
        #  certificate = acm.Certificate.from_certificate_arn(
        #      self, "new-cert-name", Fn.import_value("accountCertificateArn")
        #  )
        CfnOutput(
            self,
            "CertificateArn",
            value=self.account_cert.certificate_arn,
            export_name="accountCertificateArn",
        )
```