# OpenBao on Amazon EKS with AWS KMS and Kubernetes secret readers

Deploy a private OpenBao server with Python Pulumi, AWS KMS auto-unseal,
persistent Raft storage and a Kubernetes workload that reads one canary secret
using its service account. The workload stores no OpenBao token.

Validation status, November 15, 2026: deployed in an approved disposable EKS
environment. The authorized canary read passed; an unbound identity and an
ungranted path each returned HTTP 403. Replacing the server pod demonstrated
KMS auto-unseal and a successful read of the persisted canary. The namespace,
Raft volume, IAM role and Pod Identity association were independently confirmed
removed after destruction. The KMS key is scheduled for deletion on December 15,
2026. HA, backup, restore and production readiness remain untested.

The stack creates one KMS key, one IAM role/inline policy and EKS Pod Identity
association, a dedicated namespace, TLS Secret/public CA, three service
accounts, the official OpenBao Helm release and a sample consumer Job.
It uses OpenBao's native one-time initialization, with no bootstrap Job,
root token Secret, custom image or extra Terraform provider.

## Requirements

Use an existing EKS cluster in `us-east-2`, Kubernetes 1.30 or later, Linux
AMD64 nodes, working EKS Pod Identity agent and EBS CSI. Supply an existing
encrypted StorageClass with `ReadWriteOnce` and understand its reclaim policy.
An existing cluster and its node charges are outside this application's stack.

The deployment runner needs authenticated AWS access, private Kubernetes API
connectivity, and Kubernetes rights to create a namespace and the chart's
TokenReview ClusterRoleBinding. The server's Pod Identity role has only
Encrypt/Decrypt/DescribeKey on its one KMS key. Keep EC2 node credentials
inaccessible to application pods. The reader and operator service accounts
have no AWS association. No Dify, Tailscale, firewall agent or operator is
required.

The server declares the standard Pod Identity token projection and SDK
environment variables explicitly, so a new association does not depend on
admission-webhook cache propagation. Kubernetes rotates the projected token.

Nodes need the official Quay image. The server needs ordinary HTTPS to
`ghcr.io` and `pkg-containers.githubusercontent.com` to obtain its pinned
plugin, AWS KMS in Ohio, the Kubernetes API and its local Pod Identity agent.
Do not add private endpoints or a proxy unless your platform requires them.
No application public inbound service is created.

The supported deployment runner is CPython 3.12 on Linux glibc ARM64/AMD64
(or macOS ARM64 for independently authorized environments), Pulumi CLI/SDK
3.273.0, AWS provider 7.52.1 and Kubernetes provider 4.34.2.
The wheel lock reuses the reviewed closure and adds verified Linux AMD64
grpcio/PyYAML hashes. Provider/tool binaries and downloaded wheels must be
reviewed and scanned before execution in your approved environment.
Use existing reviewed `uv`, `kubectl` and OpenBao CLI 2.7.2. Do not run this
workload or provider programs on a workstation subject to a source-only policy.

Chart 0.30.7, server 2.7.2 and AWS KMS plugin 0.1.1 are pinned in `pins.json`.
The exact official chart archive is vendored. The server downloads the official
plugin by its AMD64 OCI manifest digest into a mounted empty directory before
unsealing; no mutable image tag or binary-download script is used.

## Configure and deploy

Authorized collaborators can clone the private repository:

```sh
git clone https://github.com/pulumi-labs/pulumi-example-stack-aws-openbao-eks.git
cd pulumi-example-stack-aws-openbao-eks
```

Public release and authored-code licensing are pending.

Choose a lowercase resource prefix of at most 30 characters, starting with a
letter. It becomes both the Kubernetes namespace and Helm release name.
If omitted, it defaults to `<Pulumi project>-<Pulumi stack>`.
Each namespace/release must be new and owned by this stack.

The commands below run in an approved deployment environment after the
artifact and execution checks required by your organization:

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --require-hashes --only-binary=:all: -r requirements.txt
pulumi stack init dev
pulumi config set resourcePrefix my-openbao
pulumi config set clusterName "$EKS_CLUSTER_NAME"
pulumi config set storageClassName "$ENCRYPTED_STORAGE_CLASS"
pulumi config set --secret kubeconfig < "$KUBECONFIG_FILE"
```

AWS resources inherit provider default tags `pulumi-project` and
`pulumi-stack`. The IAM role and inline policy are named
`<resourcePrefix>-unseal`. If your deployment identity requires an existing
permissions boundary, set its ARN. It is optional in product defaults:

```sh
pulumi config set permissionsBoundaryArn "$BOUNDARY_ARN"
```

Supply a server certificate chain, its matching private key and the trusted
CA. The certificate must cover `<prefix>.<prefix>.svc`; including `localhost`
and `127.0.0.1` also permits direct local diagnostic commands. Keep the key
outside this repository. For an isolated evaluation, create a short-lived
self-signed leaf in secure storage in the approved environment:

```sh
umask 077
prefix=my-openbao
secure_dir="$(mktemp -d)"
openssl req -x509 -newkey rsa:3072 -nodes -days 2 \
  -subj "/CN=$prefix.$prefix.svc" \
  -addext "subjectAltName=DNS:$prefix.$prefix.svc,DNS:localhost,IP:127.0.0.1" \
  -keyout "$secure_dir/server.key" -out "$secure_dir/server.crt"
pulumi config set serverCertificate < "$secure_dir/server.crt"
pulumi config set caCertificate < "$secure_dir/server.crt"
pulumi config set --secret serverPrivateKey < "$secure_dir/server.key"
```

Use a renewable organization-issued certificate for a longer-lived install.
The two-day certificate is an evaluation example, not a product expiry default.
Retain the trusted public CA file for administrative CLI access. Pulumi
encrypts the private key/kubeconfig configuration and propagates the key as a
secret input. Kubernetes and encrypted Pulumi state still contain sensitive
material; preserve their access controls and never publish state.

```sh
pulumi preview --refresh
pulumi up
namespace="$(pulumi stack output namespace)"
release="$(pulumi stack output releaseName)"
kubectl -n "$namespace" wait --for=condition=complete job/canary-consumer --timeout=120s
kubectl -n "$namespace" logs job/canary-consumer
```

Expected sample output is `Canary read authorized and value matched.` The Job
authenticates as `canary-reader`, reads KV-v2 `kv/data/canary`, verifies the
synthetic value and revokes its five-minute token. It never prints the value,
JWT or token. The KV-v2 `data` API path differs from the CLI's
`bao kv get -mount=kv canary` path.

For another application, use the same service account in this namespace,
trusted CA and Kubernetes auth role `canary-reader`. Its ACL permits exactly
one secret read and self-revocation, without the general default policy.
Do not put real secrets in `server.hcl`; its seed is public synthetic data.
Do not change the seed in configuration expecting existing storage to update.

## Initialization and operator custody

On empty Raft storage, the native `initialize` block configures KV-v2,
the canary, local Kubernetes TokenReview auth, policies and exact roles.
OpenBao creates no recovery shares during this process. Its temporary root
token is internal, is never returned and is revoked by the server's deferred
cleanup. The application stack never handles it. Keep log level at `info`;
trace logging exposes initialization request/response data.

The `openbao-operator` service account is the operator login door, not a pod.
Its policy permits changing only this canary and explicitly initializing
recovery shares. Restrict Kubernetes token creation, impersonation, pod
creation/exec and Secret reads: Kubernetes administrators can assume this
identity. There is no stored broad operator/root token.

In one approved terminal, establish the private administration path:

```sh
kubectl -n "$namespace" port-forward "service/$release" 8200:8200 --address 127.0.0.1
```

In another, use the trusted CA and service DNS name for TLS verification.
Token files stay in private temporary storage, not command arguments or logs:

```sh
set +x
umask 077
export BAO_ADDR=https://127.0.0.1:8200
export BAO_TLS_SERVER_NAME="$release.$namespace.svc"
export BAO_CACERT="$CA_FILE"
operator_jwt="$(mktemp)"
kubectl -n "$namespace" create token openbao-operator --duration=5m > "$operator_jwt"
BAO_TOKEN="$(bao write -field=token auth/kubernetes/login \
  role=canary-operator jwt=@"$operator_jwt")"
export BAO_TOKEN
rm "$operator_jwt"
bao kv put -mount=kv canary value=operator-updated-synthetic-value > /dev/null
bao token revoke -self > /dev/null
unset BAO_TOKEN
```

The sample consumer intentionally expects the initial seed. After an
intentional operator edit, update your application's expected behavior, or
write `openbao-kms-canary-v1` again before rerunning that demonstration.

Recovery is not set up by this example. If you choose to establish custody,
authenticate as the operator and use the documented
`POST /v1/sys/rotate/recovery/init` API with `secret_shares`,
`secret_threshold`, and one base64-encoded binary PGP public key per share.
Set `backup=false`; redirect the response to protected storage outside the
repository and distribute the encrypted shares to their distinct custodians.
The endpoint immediately returns shares when none exist. It must not be
invoked in a logged shell or captured as trial evidence. No recovery or
generate-root drill has been validated here.

## Updates and troubleshooting

Self-initialization runs only once. Pulumi changes to `server.hcl` update
startup configuration but do not reconcile API objects on existing Raft data.
The operator door deliberately cannot rewrite arbitrary policies or roles.
Broader configuration management requires an explicitly designed operator
policy/provider integration; this example does not claim it.

The chart uses RollingUpdate so Helm waits for server readiness before creating
the consumer. After changing TLS/configuration, restart the one server
deliberately and wait for readiness. There is an outage while it restarts;
no key or token should be needed for KMS unseal:

```sh
kubectl -n "$namespace" delete pod "$release-0"
kubectl -n "$namespace" wait --for=create "pod/$release-0" --timeout=120s
kubectl -n "$namespace" wait --for=condition=ready "pod/$release-0" --timeout=300s
kubectl -n "$namespace" exec "$release-0" -- bao status
```

If startup fails, inspect sanitized error messages for plugin download, KMS
permissions, Pod Identity credentials, certificate SAN or PVC mount issues.
Verify the existing agent and regional KMS connectivity. Do not inject static
AWS keys, disable TLS verification or substitute an image to make a check pass.
The plugin needs a writable `/openbao/plugins` directory, provided by emptyDir.
Missing auth after a partial initialization is not repaired by repeated
Pulumi updates: preserve storage and diagnose. Only reset disposable data
after intentionally accepting its loss. Disabling/deleting the KMS key makes
the Raft contents unusable.

## Cost and removal

One KMS key is approximately $1/month plus requests; the encrypted 2 GiB EBS
volume is additional. EKS and nodes dominate costs if you create a dedicated
platform. Verify current AWS pricing for your account and region. No cost cap
is enforced by this application.

Namespace and KMS key are protected by default; the Raft PVC is retained by
the chart. To remove a disposable installation, first preserve data and custody
material you need. Stop writers and intentionally authorize data deletion.
Record its PVC's bound PV and EBS volume ID before removal.

```sh
# Only after accepting destruction of this stack's data:
pulumi config set protectData false
pulumi up
kubectl -n "$namespace" scale statefulset "$release" --replicas=0
kubectl -n "$namespace" delete pvc "data-$release-0"
pulumi destroy
```

A `Retain` StorageClass leaves the PV/EBS volume for explicit owner cleanup.
A `Delete` StorageClass delegates EBS removal to CSI. Reconcile actual remaining
owned volumes either way. KMS deletion is scheduled for 30 days later; record
PendingDeletion and its effective deletion date. Keep encrypted state until
cleanup is independently checked. Do not delete the existing EKS cluster,
shared CSI, Pod Identity agent, VPC, nodes or shared access configuration
as part of this application's removal.

## Sources, licensing and maintenance

- [OpenBao 2.7.2](https://github.com/openbao/openbao/releases/tag/v2.7.2)
- [Official Helm chart 0.30.7](https://github.com/openbao/openbao-helm/releases/tag/openbao-0.30.7)
- [Official AWS KMS plugin 0.1.1](https://github.com/openbao/openbao-plugins/releases/tag/kms-aws-v0.1.1)
- [Native self-initialization](https://openbao.org/docs/configuration/self-init/)
- [Kubernetes authentication](https://openbao.org/docs/auth/kubernetes/)
- [Recovery share initialization API](https://openbao.org/docs/api/system/rotate/init/)
- [Truvity secrets](https://github.com/truvity/secrets), whose maintained
  upstream chart/Pod Identity/KMS patterns overlap this narrower integration.

Upstream license texts are preserved in `OPENBAO-LICENSE`, `HELM-LICENSE` and
`PLUGIN-LICENSE`, and the unmodified chart archive includes its notices.
These MPL-2.0 notices do not license this example's authored code.
Authored-code licensing, maintenance owner and a private security-reporting
contact must be decided before public release. Until then, report maintenance
questions through the private repository owner; do not post credentials or
state in an issue.
