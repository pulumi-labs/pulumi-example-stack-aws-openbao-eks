"""Deploy a private OpenBao server with AWS KMS and Kubernetes authentication."""

import re
from pathlib import Path
from typing import Final

import pulumi
import pulumi_aws as aws
import pulumi_kubernetes as k8s

ROOT: Final = Path(__file__).parent
SERVER_IMAGE: Final = (
    "quay.io/openbao/openbao:2.7.2@sha256:"
    "a78eb12a9fc7a3ebb4bec1724e2bdd6a28e33394820641d061166919a3f1866b"
)
config = pulumi.Config()
prefix = config.get("resourcePrefix") or f"{pulumi.get_project()}-{pulumi.get_stack()}"
if re.fullmatch(r"[a-z](?:[a-z0-9-]{0,28}[a-z0-9])?", prefix) is None:
    prefix_error: Final = "resourcePrefix needs 1-30 lowercase DNS label characters."
    raise pulumi.RunError(prefix_error)
NAMESPACE: Final = prefix
ADDRESS: Final = f"https://{NAMESPACE}.{NAMESPACE}.svc:8200"
protect_data = config.get_bool("protectData")
if protect_data is None:
    protect_data = True
cloud = aws.Provider(
    "aws-ohio",
    region="us-east-2",
    default_tags={
        "tags": {
            "pulumi-stack": pulumi.get_stack(),
            "pulumi-project": pulumi.get_project(),
        },
    },
)
cluster = aws.eks.get_cluster(
    name=config.require("clusterName"), opts=pulumi.InvokeOptions(provider=cloud)
)
account = aws.get_caller_identity(opts=pulumi.InvokeOptions(provider=cloud))
kubernetes = k8s.Provider("eks", kubeconfig=config.require_secret("kubeconfig"))
namespace = k8s.core.v1.Namespace(
    "namespace",
    metadata={"name": NAMESPACE},
    opts=pulumi.ResourceOptions(provider=kubernetes, protect=protect_data),
)
server_account = k8s.core.v1.ServiceAccount(
    "server-account",
    metadata={"name": "openbao-server", "namespace": namespace.metadata.name},
    opts=pulumi.ResourceOptions(provider=kubernetes),
)
key = aws.kms.Key(
    "unseal",
    description="OpenBao Raft auto-unseal",
    enable_key_rotation=True,
    deletion_window_in_days=30,
    tags={"Name": f"{prefix}-unseal"},
    opts=pulumi.ResourceOptions(provider=cloud, protect=protect_data),
)
role = aws.iam.Role(
    "unseal-role",
    name=f"{prefix}-unseal",
    permissions_boundary=config.get("permissionsBoundaryArn"),
    assume_role_policy=pulumi.Output.json_dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Service": "pods.eks.amazonaws.com"},
                    "Action": ["sts:AssumeRole", "sts:TagSession"],
                    "Condition": {
                        "StringEquals": {
                            "aws:SourceAccount": account.account_id,
                            "aws:RequestTag/kubernetes-namespace": NAMESPACE,
                            "aws:RequestTag/kubernetes-service-account": "openbao-server",
                        },
                        "ArnEquals": {"aws:SourceArn": cluster.arn},
                    },
                }
            ],
        }
    ),
    opts=pulumi.ResourceOptions(provider=cloud),
)
key_access = aws.iam.RolePolicy(
    "unseal-key-access",
    name=f"{prefix}-unseal",
    role=role.id,
    policy=pulumi.Output.json_dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["kms:Encrypt", "kms:Decrypt", "kms:DescribeKey"],
                    "Resource": key.arn,
                }
            ],
        }
    ),
    opts=pulumi.ResourceOptions(provider=cloud),
)
association = aws.eks.PodIdentityAssociation(
    "unseal-association",
    cluster_name=cluster.name,
    namespace=namespace.metadata.name,
    service_account=server_account.metadata.name,
    role_arn=role.arn,
    disable_session_tags=False,
    opts=pulumi.ResourceOptions(provider=cloud, depends_on=[key_access]),
)
certificate = k8s.core.v1.Secret(
    "server-tls",
    metadata={"name": "openbao-tls", "namespace": namespace.metadata.name},
    type="kubernetes.io/tls",
    string_data={
        "tls.crt": config.require("serverCertificate"),
        "tls.key": config.require_secret("serverPrivateKey"),
        "ca.crt": config.require("caCertificate"),
    },
    opts=pulumi.ResourceOptions(provider=kubernetes),
)
ca = k8s.core.v1.ConfigMap(
    "client-ca",
    metadata={"name": "openbao-ca", "namespace": namespace.metadata.name},
    data={"ca.crt": config.require("caCertificate")},
    opts=pulumi.ResourceOptions(provider=kubernetes),
)
reader = k8s.core.v1.ServiceAccount(
    "canary-reader",
    metadata={"name": "canary-reader", "namespace": namespace.metadata.name},
    opts=pulumi.ResourceOptions(provider=kubernetes),
)
operator = k8s.core.v1.ServiceAccount(
    "operator",
    metadata={"name": "openbao-operator", "namespace": namespace.metadata.name},
    opts=pulumi.ResourceOptions(provider=kubernetes),
)
server_config = key.arn.apply(
    lambda arn: (ROOT / "server.hcl")
    .read_text(encoding="utf-8")
    .replace("__KMS_KEY_ARN__", arn)
    .replace("__NAMESPACE__", NAMESPACE)
    .replace("__RELEASE__", NAMESPACE)
)
server = k8s.helm.v3.Release(
    "openbao",
    name=NAMESPACE,
    namespace=namespace.metadata.name,
    chart=str(ROOT / "chart" / "openbao-0.30.7.tgz"),
    version="0.30.7",
    timeout=900,
    value_yaml_files=[pulumi.FileAsset(str(ROOT / "values.yaml"))],
    values={
        "global": {"tlsDisable": False},
        "injector": {"enabled": False},
        "csi": {"enabled": False},
        "ui": {"enabled": False},
        "server": {
            "image": {
                "registry": "quay.io",
                "repository": "openbao/openbao",
                "tag": SERVER_IMAGE.split(":", 1)[1],
            },
            "nodeSelector": {"kubernetes.io/arch": "amd64"},
            "serviceAccount": {
                "create": False,
                "name": "openbao-server",
                "serviceDiscovery": {"enabled": False},
            },
            "authDelegator": {"enabled": True},
            "standalone": {"enabled": False},
            "ha": {
                "enabled": True,
                "replicas": 1,
                "apiAddr": ADDRESS,
                "raft": {"enabled": True, "setNodeId": True, "config": server_config},
                "disruptionBudget": {"enabled": False},
            },
            "dataStorage": {
                "enabled": True,
                "size": "2Gi",
                "storageClass": config.require("storageClassName"),
            },
            "persistentVolumeClaimRetentionPolicy": {
                "whenDeleted": "Retain",
                "whenScaled": "Retain",
            },
            "resources": {
                "requests": {"cpu": "250m", "memory": "512Mi"},
                "limits": {"cpu": "1", "memory": "1Gi"},
            },
            "logLevel": "info",
            "extraEnvironmentVars": {
                "AWS_REGION": "us-east-2",
                "AWS_EC2_METADATA_DISABLED": "true",
                "BAO_CACERT": "/openbao/tls/ca.crt",
                "BAO_TLS_SERVER_NAME": f"{NAMESPACE}.{NAMESPACE}.svc",
            },
            "service": {
                "type": "ClusterIP",
                "active": {"enabled": False},
                "standby": {"enabled": False},
            },
            "readinessProbe": {"path": "/v1/sys/health", "enabled": True},
        },
    },
    opts=pulumi.ResourceOptions(
        provider=kubernetes,
        depends_on=[association, certificate, operator, reader],
    ),
)
consumer = k8s.yaml.v2.ConfigGroup(
    "canary-consumer",
    yaml=(ROOT / "consumer.yaml")
    .read_text(encoding="utf-8")
    .replace("__NAMESPACE__", NAMESPACE)
    .replace("__SERVER_IMAGE__", SERVER_IMAGE),
    opts=pulumi.ResourceOptions(provider=kubernetes, depends_on=[server, ca]),
)
pulumi.export("namespace", namespace.metadata.name)
pulumi.export("releaseName", NAMESPACE)
pulumi.export("serverAddress", ADDRESS)
pulumi.export("unsealKeyArn", key.arn)
pulumi.export("consumerJob", "canary-consumer")
