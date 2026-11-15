# Initial data is synthetic and public. Never put actual secrets in this file.
ui = false
api_addr = "https://__RELEASE__.__NAMESPACE__.svc:8200"
cluster_addr = "https://__RELEASE__-0.__RELEASE__-internal.__NAMESPACE__.svc:8201"

listener "tcp" {
  address = "[::]:8200"
  cluster_address = "[::]:8201"
  tls_cert_file = "/openbao/tls/tls.crt"
  tls_key_file = "/openbao/tls/tls.key"
  tls_min_version = "tls12"
}
storage "raft" {
  path = "/openbao/data"
}
plugin_directory = "/openbao/plugins"
plugin_auto_download = true
plugin_download_behavior = "fail"
plugin "kms" "awskms" {
  image = "ghcr.io/openbao/openbao-plugin-kms-aws:v0.1.1@sha256:e0cff49b4670083d1ba5f3cd105b6f7967040869b6c14be2f6d9ca4d7ed220cd"
}
seal "awskms" {
  region = "us-east-2"
  kms_key_id = "__KMS_KEY_ARN__"
}
initialize "canary-bootstrap" {
  request "kv-mount" {
    operation = "update"
    path = "sys/mounts/kv"
    data = {
      type = "kv"
      options = { version = "2" }
    }
  }
  request "canary" {
    operation = "update"
    path = "kv/data/canary"
    data = { data = { value = "openbao-kms-canary-v1" } }
  }
  request "kubernetes-auth" {
    operation = "update"
    path = "sys/auth/kubernetes"
    data = { type = "kubernetes" }
  }
  request "kubernetes-config" {
    operation = "update"
    path = "auth/kubernetes/config"
    data = {
      kubernetes_host = "https://kubernetes.default.svc:443"
      disable_local_ca_jwt = false
    }
  }
  request "reader-policy" {
    operation = "update"
    path = "sys/policies/acl/canary-reader"
    data = {
      policy = <<-POLICY
        path "kv/data/canary" { capabilities = ["read"] }
        path "auth/token/revoke-self" { capabilities = ["update"] }
      POLICY
    }
  }
  request "reader-role" {
    operation = "update"
    path = "auth/kubernetes/role/canary-reader"
    data = {
      bound_service_account_names = ["canary-reader"]
      bound_service_account_namespaces = ["__NAMESPACE__"]
      token_policies = ["canary-reader"]
      token_no_default_policy = true
      token_ttl = "5m"
      token_max_ttl = "5m"
    }
  }
  request "operator-policy" {
    operation = "update"
    path = "sys/policies/acl/canary-operator"
    data = {
      policy = <<-POLICY
        path "kv/data/canary" { capabilities = ["create", "read", "update"] }
        path "sys/rotate/recovery/init" { capabilities = ["create", "read", "update", "sudo"] }
        path "auth/token/revoke-self" { capabilities = ["update"] }
      POLICY
    }
  }
  request "operator-role" {
    operation = "update"
    path = "auth/kubernetes/role/canary-operator"
    data = {
      bound_service_account_names = ["openbao-operator"]
      bound_service_account_namespaces = ["__NAMESPACE__"]
      token_policies = ["canary-operator"]
      token_no_default_policy = true
      token_ttl = "5m"
      token_max_ttl = "5m"
    }
  }
}
