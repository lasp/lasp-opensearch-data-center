# Standard
import json

# Installed
from constructs import Construct
from aws_cdk import (
    aws_route53 as route53,
    aws_certificatemanager as acm,
    RemovalPolicy,
    Environment,
    aws_iam as iam,
    aws_opensearchserverless as opensearch
)

class OpenSearchServerlessConstruct(Construct):
    def __init__(
        self, 
        scope: Construct,
        construct_id: str,
        environment: Environment,
        collections_config: dict,
        hosted_zone: route53.HostedZone,
        certificate: acm.Certificate,
        opensearch_domain_name: str,
        removal_policy: RemovalPolicy = RemovalPolicy.RETAIN,
    ) -> None:
        """
        Construct init.
        Parameters
        ----------
        scope : Construct
            The scope in which this Construct is instantiated, usually the `self` inside a Stack.
        construct_id : str
            ID for this construct instance, e.g. "MyOpenSearchConstruct".
        environment : Environment
            AWS environment (account and region).
        collections_config: dict
            A dictonary of collection name keys mapped to collection type values
        hosted_zone : route53.HostedZone
            Hosted zone to host the OpenSearch instance. Associated with a domain name (e.g. `my-domain-name.net`).
            Can be sourced from a lasp_opensearch_data_center CertificateStack.
        certificate : acm.Certificate
            Pre-defined certificate for OpenSearch. This is likely to be used elsewhere, so it is left to the user
            to define it outside of this Construct. Can be sourced from a lasp_opensearch_data_center CertificateStack.
        opensearch_domain_name : str
            Name of the OpenSearch domain, e.g. (`opensearch-testing`). Required to name the OpenSearch Domain
            but does not affect access URLs.
        """
        super().__init__(scope, construct_id)

        self.collections = {} # Key: collection name, Value: Collection endpoint 

        for collection_key, config in collections_config.items():
            collection = self._create_collection(
                collection_key,
                config,
                environment,
                opensearch_domain_name
            )
            self.collections[collection_key] = collection

        #TODO maybe build the ingest lambda env here and set it as an instance var?

    def _create_collection( # TODO add abilty to give a custom domain name
        self,
        collection_name: str,
        collection_type: str,
        enviroment: Environment,
    ) -> str:
        """
        Creates a new OpenSearch Serverless Collection
        Parameters
        ----------
        collection_name : str
            The name to give the new collection
        collection_type : str
            The type of the new collection, can be "Time Series", "Search", or "Vector Search
        environment : Environment
            AWS environment (account and region).
        
        Returns
        -------
        collection_endpoint : str
            The url of the collection endpoint
        """

        # Create security policies
        encryption_policy = self._create_encryption_policy(collection_name, collection_type)
        network_policy = self._create_network_policy(collection_name, collection_type) 
        access_policy = self._create_access_policy(collection_name, collection_type)

        # Create collection
        collection = opensearch.CfnCollection(
            self, f'{collection_name}-Collection',
            name=collection_name,
            type=collection_type,
        )
        
        # Set dependencies
        collection.add_dependency(encryption_policy)
        collection.add_dependency(network_policy)
        collection.add_dependency(access_policy)

        return collection.attr_collection_endpoint
    
    def _create_encryption_policy(
        self, 
        collection_name: str, 
        collection_type: str
    ): #TODO add return type and docstring
        encryption_policy = opensearch.CfnSecurityPolicy(
            self, f"opensearch-serverless-{collection_name}-encryption-policy",
            name= f"{collection_name}-encryption-policy",
            type="encryption",
            policy=json.dumps({
                "Rules": [{
                    "ResourceType":"collection",
                    "Resource":[f"collection/{collection_name}"]
                }],
                "AWSOwnedKey": True
            })
        )
        return encryption_policy

    def _create_network_policy(
        self, 
        collection_name: str, 
        collection_type: str
    ): #TODO add return type and docstring
        network_policy = opensearch.CfnSecurityPolicy(
            self, f"opensearch-serverless-{collection_name}-network-policy",
            name= f"{collection_name}-network-policy",
            type="network",
            policy=json.dumps({
                "Rules": [{
                    "ResourceType":"collection",
                    "Resource":[f"collection/{collection_name}"]
                }],
                "AllowFromPublic": True  #TODO or VPC configuration here, need to make sure that the same access rules will apply to servereless IP range etc
            })
        )
        return network_policy

    def _create_access_policy(
        self, 
        collection_name: str, 
        collection_type: str
    ): #TODO add return type and docstring
        access_policy = opensearch.CfnAccessPolicy(
            self, f"opensearch-serverless-{collection_name}-access-policy",
            name= f"{collection_name}-access-policy",
            type="data",
            policy=json.dumps({
                "Rules": [{
                    "ResourceType": "index",
                    "Resource": [f"index{collection_name}-collection/*"],
                    "Permission": ["aoss:CreateIndex", "aoss:ReadDocument", "aoss:WriteDocument"] #TODO might need to add solme privileges 
                }],
                "Principal":[iam.AnyPrincipal()] #TODO probably need to restrict this further
            })
        )

        return access_policy