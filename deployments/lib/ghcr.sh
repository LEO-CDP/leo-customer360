#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# ghcr.sh — shared GHCR image resolution for the CD (pull-from-registry) path.
# Source this from a module deploy script (e.g. `. ../lib/ghcr.sh`).
#
# Deploy mode is chosen by the caller:
#   BUILD_LOCAL=1  -> keep the legacy build-on-the-VM path (ships source, builds).
#   (default)      -> pull the CI-built image from GHCR by tag/digest.
#
# Tag precedence (highest first):  IMAGE_TAG env  >  `image_tag` in the module's
# overlays/<env>.tfvars  >  "latest".
#
# The image name in GHCR matches the CI workflow:
#   ghcr.io/leo-cdp/leo-customer360/<service>
# ---------------------------------------------------------------------------
GHCR_REGISTRY="${GHCR_REGISTRY:-ghcr.io/leo-cdp/leo-customer360}"
GHCR_ORG="${GHCR_ORG:-LEO-CDP}"
GHCR_REPO_NAME="${GHCR_REPO_NAME:-leo-customer360}"

# image_ref <service> <tag>
#   Echo the image reference to deploy. When RESOLVE_DIGEST=1 and `gh` is present,
#   the tag is resolved to an immutable @sha256 digest (newest push for the
#   package); otherwise the plain :<tag> reference is returned.
image_ref() {
  local svc="$1" tag="${2:-latest}" digest=""
  if [ "${RESOLVE_DIGEST:-0}" = "1" ] && command -v gh >/dev/null 2>&1; then
    digest="$(gh api -H 'Accept: application/vnd.github+json' \
      "/orgs/${GHCR_ORG}/packages/container/${GHCR_REPO_NAME}%2F${svc}/versions?per_page=1" \
      --jq '.[0].name' 2>/dev/null || true)"
  fi
  if [ -n "$digest" ]; then
    printf '%s/%s@%s' "$GHCR_REGISTRY" "$svc" "$digest"
  else
    printf '%s/%s:%s' "$GHCR_REGISTRY" "$svc" "$tag"
  fi
}

# resolve_tag <overlay-file>
#   Resolve the tag for the current deploy: IMAGE_TAG env, else `image_tag` from
#   the given overlays/<env>.tfvars (uses the caller's tfval), else "latest".
resolve_tag() {
  local ovl="$1" t="${IMAGE_TAG:-}"
  if [ -z "$t" ] && command -v tfval >/dev/null 2>&1; then t="$(tfval image_tag "$ovl" 2>/dev/null)"; fi
  printf '%s' "${t:-latest}"
}
