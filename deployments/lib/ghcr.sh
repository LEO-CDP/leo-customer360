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
#   Echo the image reference to deploy. With RESOLVE_DIGEST=1 (the DEFAULT) and `gh`
#   present + GH package:read, <tag> is resolved to the immutable @sha256 digest of the
#   version that ACTUALLY carries <tag> — NOT merely the newest push. (A buildx multi-arch
#   push creates several package versions at the same instant: the tagged manifest index
#   plus untagged per-arch/attestation manifests, so `.[0]` / "newest" can pin the wrong,
#   untagged digest.) Deploying by digest makes CD unambiguous + verifiable: the exact
#   bytes are pinned, and the box can't silently reuse a stale mutable tag.
#   Falls back to the plain :<tag> reference (and says why on stderr) when gh is absent,
#   lacks package:read, or the tag isn't found — so deploys never hard-fail on resolution.
#   Set RESOLVE_DIGEST=0 to force the plain mutable tag.
image_ref() {
  local svc="$1" tag="${2:-latest}" digest=""
  if [ "${RESOLVE_DIGEST:-1}" = "1" ] && command -v gh >/dev/null 2>&1; then
    digest="$(gh api -H 'Accept: application/vnd.github+json' \
      "/orgs/${GHCR_ORG}/packages/container/${GHCR_REPO_NAME}%2F${svc}/versions?per_page=100" \
      --jq "[.[] | select(.metadata.container.tags | index(\"$tag\"))][0].name // empty" 2>/dev/null || true)"
  fi
  if [ -n "$digest" ]; then
    printf '%s/%s@%s' "$GHCR_REGISTRY" "$svc" "$digest"
    printf '>> image_ref: %s:%s pinned -> @%s\n' "$svc" "$tag" "$digest" 1>&2
  else
    printf '%s/%s:%s' "$GHCR_REGISTRY" "$svc" "$tag"
    [ "${RESOLVE_DIGEST:-1}" = "1" ] && printf '>> image_ref: %s:%s NOT pinned (gh missing / no package:read / tag absent) — using mutable tag\n' "$svc" "$tag" 1>&2
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
