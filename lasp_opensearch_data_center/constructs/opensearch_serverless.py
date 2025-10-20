# Standard
import json
from typing import Optional, List

# Installed
from constructs import Construct
from aws_cdk import (
    aws_route53 as route53,
    aws_certificatemanager as acm,
    RemovalPolicy,
    Environment,
    aws_iam as iam,
    aws_opensearchserverless as opensearch,
    aws_ec2 as ec2
)


class OpenSearchServerlessConstruct(Construct):
    def __init__(
        self, 
        scope: Construct,
        construct_id: str,
    ) -> None:
        """
        Construct init.
        Parameters
        ----------
        scope : Construct
            The scope in which this Construct is instantiated, usually the `self` inside a Stack.
        construct_id : str
            ID for this construct instance, e.g. "MyOpenSearchConstruct".
        """

        super().__init__(scope, construct_id)

        collection_name="liberaitdc-collection"

        # Create the policies for the collection
        encryption_policy = opensearch.CfnSecurityPolicy(
            self, "OpenSearchServerless-encryption-policy",
            name="liberaitdc-encryption-policy",
            type="encryption",
            policy=json.dumps({
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{collection_name}"]
                }],
                "AWSOwnedKey": True
            })
        )


        network_policy = opensearch.CfnSecurityPolicy(
            self, "OpenSearchServerless-network-policy",
            name="liberaitdc-network-policy",
            type="network",
            policy=json.dumps([{
                "Rules": [{
                    "ResourceType": "collection",
                    "Resource": [f"collection/{collection_name}"]
                }, {
                    "ResourceType": "dashboard",
                    "Resource": [f"collection/{collection_name}"]
                }],
                "AllowFromPublic": True
            }])
        )

        access_policy = opensearch.CfnAccessPolicy(
            self, "OpenSearchServerless-access-policy",
            name= "liberaitdc-access-policy",
            type="data",
            policy=json.dumps([{
                "Rules": [{
                    "ResourceType": "index",
                    "Resource": [f"index/{collection_name}/*"],
                    "Permission": ["aoss:CreateIndex", "aoss:ReadDocument", "aoss:WriteDocument", "aoss:UpdateIndex", "aoss:DeleteIndex", "aoss:DescribeIndex"] #TODO might need to add some more privileges 
                }],
                "Principal":[f"arn:aws:iam::983496429036:root"] #TODO probably need to restrict this further to specific IAM roles/users
            }])
        )

        # Create the new collection
        self.collection = opensearch.CfnCollection( 
            self, "OpenSearchServerlessCollection",
            name=collection_name,
            type="SEARCH",
        )
        
        # attach policies to collection
        self.collection.add_dependency(encryption_policy)
        self.collection.add_dependency(network_policy)
        self.collection.add_dependency(access_policy)

    @property
    def domain_name(self) -> str:
        """Return collection ID to maintain compatibility with provisioned OpenSearch"""
        return self.collection.attr_id  # or self.collection.collection_name
    
    @property
    def domain_endpoint(self) -> str:
        """Return the collection endpoint"""
        return self.collection.attr_collection_endpoint
    
    @property
    def domain_arn(self) -> str:
        """Return the collection ARN"""
        return self.collection.attr_arn