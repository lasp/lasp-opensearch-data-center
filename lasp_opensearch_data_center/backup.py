"""CDK resources for the AWS Backup vault"""
# Standard
from pathlib import Path

# Installed
from constructs import Construct
from aws_cdk import (
    Environment,
    Stack,
    RemovalPolicy,
    aws_backup as backup,
)


class BackupConstruct(Construct):
    """Construct containing resource(s) used to create an AWS Backup Vault"""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        """Creates a new backup vault. We will create a single vault for each AWS region
        and each AWS service will create their own backup plan within that vault.

        Parameters
        ----------
        service_name : none

        Returns
        -------
        backup_vault : backup.BackupVault
        """

        # AWS Backup vault to store various AWS service backups
        self.backup_vault = backup.BackupVault(
            self,
            "Backup_Vault",
            backup_vault_name="BackupVault",
            # We want to retain the backups if the stack is destroyed
            # If this stack is destroyed, it will require manual intervention to remove the vault first
            removal_policy=RemovalPolicy.RETAIN,
        )
