# Maintaining this OpenBao example

- Python Pulumi targets an existing EKS cluster in Ohio. Keep the explicit
  providers, inherited pulumi-project/pulumi-stack AWS tags, optional
  permissionsBoundaryArn and resourcePrefix naming. No shared platform creation.
- Runtime closure: Pulumi.yaml, __main__.py, values.yaml, server.hcl, consumer.yaml,
  requirements.txt/requirements.lock, chart/openbao-0.30.7.tgz. Preserve pins,
  upstream licenses and README. pyproject.toml contains static check settings.
- The official KMS plugin is external on OpenBao 2.7. Native OCI download occurs
  before seal catalog construction. Preserve AMD64 digest pins, mounted plugin
  directory, TLS verification, three exact-key KMS actions and scoped Pod Identity.
- Native initialize is one-time only. Its internal temporary root token is
  revoked by OpenBao and must never be exported/logged. No initial recovery keys.
  Retain the narrow operator door and the reader's exact-path policy.
- Keep actual secrets out of HCL, manifests, logs and this repository. Only the
  synthetic seed is public. Private key and kubeconfig use secret Pulumi inputs.
- No host workload/SDK/provider/tests or dependency installation under the
  source-only research contract. Primary owns scans and approved guest checks.
  Never describe unrun deployment, deny, persistence or cleanup checks as passed.
- One replica is not HA. Do not add PKI, backups, custom images, retry sidecars,
  providers or an infrastructure framework as incidental prerequisites.
- Keep the polished runtime byte-identical after any change. Tests/research
  evidence belong outside this runtime. Preserve the MIT License and upstream notices.
