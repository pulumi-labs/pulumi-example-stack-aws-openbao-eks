# OpenBao on Amazon EKS with AWS KMS and Kubernetes secret readers

This example gives your Kubernetes applications a private secrets server that
unlocks itself after a restart and lets each workload read only the secrets it
was granted. Workloads log in with their Kubernetes service account, so no
long-lived OpenBao token needs to be stored in a Kubernetes Secret. The consumer
obtains a short-lived token at runtime and revokes it after use.

You deploy it with Python Pulumi into an EKS cluster you already run. When it
finishes, a sample Job proves the setup works by reading one test secret
(the "canary") and checking its value.

## Key terms

If some of these tools are new to you, here's what each one does in this example:

- **OpenBao**: an open source secrets manager (a community fork of HashiCorp
  Vault). Applications ask it for secrets over HTTPS.
- **EKS** (Amazon Elastic Kubernetes Service): AWS's managed Kubernetes. You
  bring an existing cluster; this stack doesn't create one.
- **KMS** (AWS Key Management Service): stores the encryption key that protects
  OpenBao's data. The key never leaves AWS.
- **Auto-unseal**: OpenBao starts "sealed" and can't read its own data until it
  is unlocked. With auto-unseal, OpenBao asks KMS to unlock it, so nobody has to
  type in unseal keys after every restart.
- **Raft**: OpenBao's built-in storage engine. Here it writes to one persistent
  disk (an EBS volume), so data survives pod restarts.
- **Pod Identity** (EKS Pod Identity): gives a specific Kubernetes service
  account short-lived AWS credentials. Only the OpenBao server gets an AWS role,
  and that role can only use its one KMS key.
- **Helm**: the Kubernetes package manager. The stack installs the official
  OpenBao Helm chart, which is vendored in this repository.
- **TLS**: all traffic to OpenBao is encrypted, and clients verify the server's
  certificate against a CA you provide.

## What the stack creates

- One KMS key, plus one IAM role with an inline policy and an EKS Pod Identity
  association for the OpenBao server.
- A dedicated Kubernetes namespace, a TLS Secret and public CA, and three
  service accounts (server, `canary-reader` and `openbao-operator`).
- The official OpenBao Helm release, running one server.
- A sample consumer Job that reads the canary secret.

OpenBao sets itself up on first start with its native one-time initialization.
There's no bootstrap Job, no root token Secret, no custom image and no extra
Terraform provider.

## Prerequisites

### Cluster

- An existing EKS cluster in `us-east-2`, running Kubernetes 1.30 or later.
- Linux AMD64 nodes.
- A working EKS Pod Identity agent and EBS CSI driver.
- An existing encrypted StorageClass with `ReadWriteOnce` access. Check its
  reclaim policy, because it decides what happens to the disk on removal.

The cluster and its node charges sit outside this stack.

### Permissions

- The machine that runs Pulumi needs authenticated AWS access and private
  network access to the Kubernetes API.
- Its Kubernetes identity must be able to create a namespace and the chart's
  TokenReview ClusterRoleBinding.
- Keep EC2 node credentials out of reach of application pods. The server's
  Pod Identity role only has Encrypt, Decrypt and DescribeKey on its own KMS
  key. The reader and operator service accounts have no AWS access at all.

You don't need Dify, Tailscale, a firewall agent or a Kubernetes operator.

The server declares the standard Pod Identity token projection and SDK
environment variables itself, so a new association doesn't depend on
admission-webhook cache propagation. Kubernetes rotates the projected token.

### Network

- Nodes pull the official OpenBao image from Quay.
- The server needs ordinary HTTPS to `ghcr.io` and
  `pkg-containers.githubusercontent.com` to fetch its pinned KMS plugin, plus
  access to AWS KMS in Ohio, the Kubernetes API and its local Pod Identity agent.
- You don't need private endpoints or a proxy unless your platform requires them.
- The stack creates no public inbound service.

### Tools and versions

- CPython 3.12 on Linux glibc ARM64 or AMD64 (or macOS ARM64 in an
  independently authorized environment).
- Pulumi CLI and SDK 3.273.0, AWS provider 7.52.1, Kubernetes provider 4.34.2.
- `uv`, `kubectl` and the OpenBao CLI 2.7.2.
- `openssl`, if you generate the evaluation certificate below.

The wheel lock reuses the reviewed dependency set and adds verified Linux AMD64
grpcio and PyYAML hashes. Review and scan provider and tool binaries and the
downloaded wheels before running them in your approved environment. Don't run
the workload or provider programs on a workstation that only allows source
review.

Chart 0.30.7, server 2.7.2 and AWS KMS plugin 0.1.1 are pinned in `pins.json`.
The official chart archive is vendored. Before unsealing, the server downloads
the official plugin by its AMD64 OCI manifest digest into a mounted empty
directory. No mutable image tag or download script is used.

## Setup

### 1. Get the code

```sh
git clone https://github.com/pulumi-labs/pulumi-example-stack-aws-openbao-eks.git
cd pulumi-example-stack-aws-openbao-eks
```

### 2. Install dependencies and set basic configuration

Run these commands in your approved deployment environment, after the artifact
and execution checks your organization requires.

Pick a resource prefix: lowercase, at most 30 characters, starting with a
letter. It becomes both the Kubernetes namespace and the Helm release name, so
it must be new and owned only by this stack. If you leave it out, it defaults to
`<Pulumi project>-<Pulumi stack>`.

```sh
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python --require-hashes --only-binary=:all: -r requirements.txt
pulumi stack init dev
pulumi config set resourcePrefix my-openbao
pulumi config set clusterName "$EKS_CLUSTER_NAME"
pulumi config set storageClassName "$ENCRYPTED_STORAGE_CLASS"
pulumi config set --secret kubeconfig < "$KUBECONFIG_FILE"
```

AWS resources get the provider default tags `pulumi-project` and
`pulumi-stack`. The IAM role and inline policy are named
`<resourcePrefix>-unseal`.

### 3. (Optional) Set a permissions boundary

Only needed if your deployment identity must attach an existing permissions
boundary to new roles:

```sh
pulumi config set permissionsBoundaryArn "$BOUNDARY_ARN"
```

### 4. Provide TLS certificates

OpenBao needs a server certificate chain, its matching private key and the
trusted CA. The certificate must cover `<prefix>.<prefix>.svc`. Adding
`localhost` and `127.0.0.1` also lets you run local diagnostic commands
directly. Keep the private key outside this repository.

For a short isolated evaluation, you can create a self-signed certificate in
secure storage inside the approved environment:

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

The two-day lifetime is only for evaluation, not a product default. For a
longer-lived install, use a renewable certificate issued by your organization.
Keep the trusted public CA file, because you'll need it for CLI access later.

Pulumi encrypts the private key and kubeconfig in its configuration and passes
the key on as a secret. Kubernetes and the encrypted Pulumi state still hold
sensitive material, so keep their access controls tight and never publish state.

### 5. Preview and deploy

```sh
pulumi preview --refresh
pulumi up
namespace="$(pulumi stack output namespace)"
release="$(pulumi stack output releaseName)"
kubectl -n "$namespace" wait --for=condition=complete job/canary-consumer --timeout=120s
kubectl -n "$namespace" logs job/canary-consumer
```

Read the preview before you apply it.

### 6. Check the result

Success looks like this in the Job log:

```text
Canary read authorized and value matched.
```

Behind that line, the Job logged in as `canary-reader`, read KV-v2 path
`kv/data/canary`, checked the synthetic value and revoked its five-minute
token. It never prints the value, JWT or token. Note that the KV-v2 `data` API
path differs from the CLI form, `bao kv get -mount=kv canary`.

## Use it from your own application

Run your app in this namespace with the `canary-reader` service account, the
trusted CA and Kubernetes auth role `canary-reader`. Its ACL allows exactly one
secret read plus revoking its own token, and it doesn't include the general
default policy.

Don't put real secrets in `server.hcl`. Its seed is public synthetic data.
Changing the seed in configuration won't update storage that already exists.

## How initialization and operator access work

On empty Raft storage, OpenBao's native `initialize` block sets up KV-v2, the
canary, local Kubernetes TokenReview auth, policies and exact roles. It creates
no recovery shares. The temporary root token it uses is internal, is never
returned and is revoked by the server's deferred cleanup, so the Pulumi stack
never sees it. Keep the log level at `info`, because trace logging exposes
initialization request and response data.

The `openbao-operator` service account is how an operator logs in. It isn't a
pod. Its policy can change only the canary and explicitly initialize recovery
shares. No broad operator or root token is stored anywhere. Kubernetes
administrators can assume this identity, so restrict who can create tokens,
impersonate, create or exec into pods, and read Secrets.

### Update the canary as an operator

In one approved terminal, open a private connection to OpenBao:

```sh
kubectl -n "$namespace" port-forward "service/$release" 8200:8200 --address 127.0.0.1
```

In a second terminal, log in with a short-lived service account token and
update the canary. TLS is verified with the trusted CA and the service DNS
name. Token files stay in private temporary storage, never in command
arguments or logs:

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

The sample consumer expects the original seed. After an intentional edit,
either update your application's expected value or write
`openbao-kms-canary-v1` back before rerunning the demonstration.

### Recovery shares (not set up)

This example doesn't set up recovery. If you want recovery custody, log in as
the operator and call the documented `POST /v1/sys/rotate/recovery/init` API
with `secret_shares`, `secret_threshold` and one base64-encoded binary PGP
public key per share. Set `backup=false`. Send the response straight to
protected storage outside the repository, then hand the encrypted shares to
separate custodians.

The endpoint returns shares immediately when none exist, so never call it from
a logged shell or capture its output in shared logs. Recovery custody and
generate-root drills are outside this example's setup.

## Updating

Self-initialization runs only once. Pulumi changes to `server.hcl` update the
startup configuration but don't reconcile API objects in existing Raft data.
The operator identity deliberately can't rewrite arbitrary policies or roles.
Broader configuration management would need a purpose-built operator policy or
provider integration, which this example doesn't include.

The chart uses RollingUpdate, so Helm waits for the server to be ready before it
creates the consumer. After changing TLS or configuration, restart the single
server on purpose and wait for it to become ready. Expect an outage during the
restart. KMS unseals it without any key or token:

```sh
kubectl -n "$namespace" delete pod "$release-0"
kubectl -n "$namespace" wait --for=create "pod/$release-0" --timeout=120s
kubectl -n "$namespace" wait --for=condition=ready "pod/$release-0" --timeout=300s
kubectl -n "$namespace" exec "$release-0" -- bao status
```

## Troubleshooting

- **Server won't start**: check sanitized error messages for plugin download,
  KMS permission, Pod Identity credential, certificate SAN or PVC mount
  problems. Confirm the Pod Identity agent is running and KMS in the region is
  reachable.
- **Plugin errors**: the plugin needs a writable `/openbao/plugins` directory,
  which an emptyDir provides.
- **Auth missing after a partial initialization**: repeated `pulumi up` runs
  won't fix this. Keep the storage and diagnose. Reset disposable data only
  after you've accepted losing it.
- **Don't work around failures** by adding static AWS keys, turning off TLS
  verification or swapping in another image.

If the KMS key is disabled or deleted, the Raft data can no longer be read.

## Costs

One KMS key costs about $1/month plus requests. The encrypted 2 GiB EBS volume
is extra. If you create a dedicated EKS cluster for this, the cluster and nodes
will be most of the bill. Check current AWS pricing for your account and region.
The application enforces no cost cap.

## Cleanup

The namespace and KMS key are protected by default, and the chart retains the
Raft PVC. Before removing a disposable installation:

1. Save any data and custody material you still need.
2. Stop anything that writes secrets, and decide deliberately that the data can
   be deleted.
3. Note the PVC's bound PV and EBS volume ID.

Then remove the stack:

```sh
# Only after accepting destruction of this stack's data:
pulumi config set protectData false
pulumi up
kubectl -n "$namespace" scale statefulset "$release" --replicas=0
kubectl -n "$namespace" delete pvc "data-$release-0"
pulumi destroy
```

What happens next depends on your StorageClass:

- With `Retain`, the PV and EBS volume stay behind for you to delete.
- With `Delete`, the CSI driver removes the EBS volume.

Either way, confirm which owned volumes actually remain. KMS schedules key
deletion for 30 days later, so record the PendingDeletion state and its
deletion date. Keep the encrypted Pulumi state until you've independently
checked cleanup.

Don't delete the existing EKS cluster, shared CSI driver, Pod Identity agent,
VPC, nodes or shared access configuration as part of this removal.

## Sources

- [OpenBao 2.7.2](https://github.com/openbao/openbao/releases/tag/v2.7.2)
- [Official Helm chart 0.30.7](https://github.com/openbao/openbao-helm/releases/tag/openbao-0.30.7)
- [Official AWS KMS plugin 0.1.1](https://github.com/openbao/openbao-plugins/releases/tag/kms-aws-v0.1.1)
- [Native self-initialization](https://openbao.org/docs/configuration/self-init/)
- [Kubernetes authentication](https://openbao.org/docs/auth/kubernetes/)
- [Recovery share initialization API](https://openbao.org/docs/api/system/rotate/init/)
- [Truvity secrets](https://github.com/truvity/secrets), a maintained project
  whose upstream chart, Pod Identity and KMS patterns overlap this narrower
  integration.

## License

This example's authored code is licensed under the [MIT License](LICENSE) and
is provided as-is, without warranty or a support commitment. Third-party
components keep their own licenses and notices, including `OPENBAO-LICENSE`,
`HELM-LICENSE` and `PLUGIN-LICENSE`.
