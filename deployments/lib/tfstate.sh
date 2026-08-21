#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# tfstate.sh — keep LOCAL Terraform runs aligned with the REMOTE (vStorage)
# state that CI uses. Source this from deploy-all.sh (and any script that reads
# `terraform output`) so a local run can never drift onto a stale local copy.
#
# It does two things, both idempotent:
#   1. ensure_vstorage_creds — put the vStorage S3 creds into AWS_* (if unset),
#      read from the storage module's gitignored config, so the s3 backend can
#      authenticate. Never prints them.
#   2. ensure_remote_init    — `terraform init` each remote-backend module so it
#      binds to the REMOTE state (not a stale terraform.tfstate.d/ copy). A no-op
#      when the module is already initialised.
# ---------------------------------------------------------------------------

# Modules whose state lives in the S3/vStorage remote backend (see their backend.tf).
TF_REMOTE_MODULES="${TF_REMOTE_MODULES:-server postgres cache}"

# ensure_vstorage_creds <deployments-dir>
#   Export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY from storage/terraform.tfvars
#   (or storage/.env) when they are not already in the environment.
ensure_vstorage_creds() {
  if [ -n "${AWS_ACCESS_KEY_ID:-}" ] && [ -n "${AWS_SECRET_ACCESS_KEY:-}" ]; then return 0; fi
  local base="${1:-.}" tfv="${1:-.}/storage/terraform.tfvars" env="${1:-.}/storage/.env" ak sk
  ak="$(sed -n 's/^[[:space:]]*access_key[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$tfv" 2>/dev/null | head -1)"
  sk="$(sed -n 's/^[[:space:]]*secret_key[[:space:]]*=[[:space:]]*"\([^"]*\)".*/\1/p' "$tfv" 2>/dev/null | head -1)"
  if [ -z "$ak" ]; then ak="$(sed -n 's/^[[:space:]]*\(TF_VAR_\)\{0,1\}access_key[[:space:]]*=[[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}[[:space:]]*$/\2/p' "$env" 2>/dev/null | head -1)"; fi
  if [ -z "$sk" ]; then sk="$(sed -n 's/^[[:space:]]*\(TF_VAR_\)\{0,1\}secret_key[[:space:]]*=[[:space:]]*"\{0,1\}\([^"]*\)"\{0,1\}[[:space:]]*$/\2/p' "$env" 2>/dev/null | head -1)"; fi
  [ -n "$ak" ] && export AWS_ACCESS_KEY_ID="$ak"
  [ -n "$sk" ] && export AWS_SECRET_ACCESS_KEY="$sk"
  export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-us-east-1}"
  if [ -z "${AWS_ACCESS_KEY_ID:-}" ] || [ -z "${AWS_SECRET_ACCESS_KEY:-}" ]; then
    echo "WARN: vStorage creds not found (storage/terraform.tfvars or storage/.env) and AWS_* unset — remote state reads will fail. Export AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY." >&2
    return 1
  fi
}

# ensure_remote_init <deployments-dir>
#   Bind every remote-backend module to the remote state. Idempotent: a no-op
#   when already initialised; on a fresh checkout it connects (no state copy).
ensure_remote_init() {
  local base="${1:-.}" m
  for m in $TF_REMOTE_MODULES; do
    [ -d "$base/$m" ] || continue
    if ! terraform -chdir="$base/$m" init -input=false >/dev/null 2>&1; then
      echo "WARN: 'terraform init' failed for '$m' — check vStorage creds and that the state bucket exists; local run may not match remote state." >&2
    fi
  done
}
