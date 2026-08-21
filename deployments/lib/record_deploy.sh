#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# record_deploy.sh — write a release record to the GitHub Deployments API so
# EVERY deploy (manual or CD) is tracked: env, service, image tag/digest, who,
# when, and source (cd|manual). The repo's Environments tab becomes the history
# UI, and the Deployments API is queryable for a custom UI later.
#
# Source this from a deploy script and call after the container is confirmed up:
#   record_deployment <env> <service> <image_ref> [status]   # status: success|failure|in_progress (default success)
#
# Best-effort by design: it NEVER fails the deploy. If `gh`/token/python3 are
# missing or the API call fails, it warns and returns 0.
#
# Auth: in CI, gh uses GH_TOKEN (set it to ${{ github.token }}) + the workflow
# needs `permissions: deployments: write`. Locally it uses your `gh auth` login.
# ---------------------------------------------------------------------------

record_deployment() {
  local env="$1" service="$2" image="$3" status="${4:-success}"
  command -v gh      >/dev/null 2>&1 || { echo "   (release-log: gh not found — skipped)" >&2; return 0; }
  command -v python3 >/dev/null 2>&1 || { echo "   (release-log: python3 not found — skipped)" >&2; return 0; }

  local repo="${GITHUB_REPOSITORY:-}"
  [ -n "$repo" ] || repo="$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo 'LEO-CDP/leo-customer360')"

  local source actor tag digest ref prod
  if [ -n "${GITHUB_ACTIONS:-}" ]; then source="cd"; else source="manual"; fi
  actor="${GITHUB_ACTOR:-$(git config user.name 2>/dev/null || whoami)}"

  # split the image ref into tag / digest
  case "$image" in
    *@sha256:*) digest="${image##*@}"; tag="" ;;
    *:*)        tag="${image##*:}";    digest="" ;;
    *)          tag="${image:-unknown}"; digest="" ;;
  esac

  # a git ref GitHub can attach the deployment to
  case "$tag" in
    sha-*)   ref="${tag#sha-}" ;;                                   # sha-<40hex> -> the commit
    v[0-9]*) ref="$tag" ;;                                          # release tag
    *)       ref="${GITHUB_SHA:-$(git rev-parse HEAD 2>/dev/null || echo main)}" ;;
  esac
  [ "$env" = "prod" ] && prod=true || prod=false

  # build the deployment body safely (json.dumps escapes everything)
  local body
  body="$(python3 - "$ref" "$env" "$prod" "$service" "$image" "$tag" "$digest" "$source" "$actor" <<'PY'
import json, sys
ref, env, prod, service, image, tag, digest, source, actor = sys.argv[1:10]
print(json.dumps({
    "ref": ref, "environment": env, "auto_merge": False, "required_contexts": [],
    "production_environment": prod == "true", "transient_environment": False,
    "description": (f"{service} {tag or digest} ({source})")[:140],
    "payload": {"service": service, "image": image, "tag": tag,
                "digest": digest, "source": source, "actor": actor},
}))
PY
)" || { echo "   (release-log: could not build payload — skipped)" >&2; return 0; }

  local did
  did="$(printf '%s' "$body" | gh api "repos/$repo/deployments" --method POST --input - --jq '.id // empty' 2>/dev/null || true)"
  if [ -z "$did" ]; then echo "   (release-log: deployment create failed for $service — skipped)" >&2; return 0; fi

  printf '{"state":"%s","environment":"%s","description":"%s"}' \
    "$status" "$env" "$service ${tag}${digest} @ $(date -u +%FT%TZ) by $actor ($source)" \
    | gh api "repos/$repo/deployments/$did/statuses" --method POST --input - >/dev/null 2>&1 || true

  echo "   release-log: $service ${tag}${digest} -> $env ($status, $source) [deployment $did]"
}
