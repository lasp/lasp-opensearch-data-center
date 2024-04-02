from aws_cdk import (
    Stack,
    aws_certificatemanager as acm,
    CfnOutput,
    Environment,
    aws_route53 as route53,
)
from constructs import Construct


class CertificateConstruct(Construct):
    """
    Manage certificates needed for this application.

    This class generates the SSL certificates that the application requires for SSL.
    This certificate will be used anytime an AWS resource is accessed via https.

    Notes: If this construct fails in "Pending Validation" the domain registration may not be complete
    You can check the status of the Route53 domain name with the following command:
    `whois domain_name|grep -i status`

    """

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        domain_name: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Import hosted zone which was created automatically by AWS
        # during domain registration
        self.hosted_zone = route53.HostedZone.from_lookup(
            self,
            "HostedZone",
            domain_name=domain_name,  # [dev|prod].[domain-name].com
        )

        # Create a single multi-use cert for this account: *.domain_name
        # in the default region set by the app using os.environ["CDK_DEPLOY_REGION"]
        self.account_cert = acm.Certificate(
            self,
            "default_cert",
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
