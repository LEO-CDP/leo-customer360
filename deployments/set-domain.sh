#!/usr/bin/env bash
# Change the platform public domain in ONE place and propagate it across every
# deployment overlay for an environment.
#
# The single source of truth is `caddy_domain` in proxy/overlays/<env>.tfvars.
# This script reads the CURRENT domain from there, then rewrites every place the
# domain literal appears in the env overlays: the functional values in
# proxy/sso/frontend/monitoring, plus the comment-only mentions in
# ads-server/load_balancer (kept in sync so nothing reads stale). The /auth,
# /c360api, /ads suffixes and the https:// scheme are preserved.
#
#   ./set-domain.sh <new-domain> [env]     # env defaults to uat
#   ./set-domain.sh cdp.example.com uat
#   ./set-domain.sh --dry-run new.example.com uat   # show what would change, write nothing
#
# It does NOT deploy anything and does NOT touch docs (README/diagram) or the
# point-in-time cutover patch — only the live overlay config. Review `git diff`
# and redeploy per proxy/README.md afterward (the script prints the exact order).
set -euo pipefail
cd "$(dirname "$0")"

DRY=false
[[ "${1:-}" == "--dry-run" ]] && { DRY=true; shift; }
NEW="${1:-}"; ENV="${2:-uat}"
[[ -n "$NEW" ]] || { echo "Usage: ./set-domain.sh [--dry-run] <new-domain> [env]"; exit 1; }
case "$NEW" in
  *[!a-zA-Z0-9.-]*) echo "ERROR: '$NEW' is not a bare hostname (letters, digits, dots, hyphens only)."; exit 1 ;;
esac

ovl_proxy="proxy/overlays/${ENV}.tfvars"
[[ -f "$ovl_proxy" ]] || { echo "ERROR: $ovl_proxy not found — is '$ENV' a real env?"; exit 1; }

# current domain = the caddy_domain value (source of truth)
OLD="$(grep -E '^[[:space:]]*caddy_domain[[:space:]]*=' "$ovl_proxy" | head -1 | sed -E 's/.*=[[:space:]]*"([^"]*)".*/\1/')"
[[ -n "$OLD" ]] || { echo "ERROR: could not read caddy_domain from $ovl_proxy."; exit 1; }
if [[ "$OLD" == "$NEW" ]]; then echo "Domain is already '$NEW' for env '$ENV' — nothing to do."; exit 0; fi

# escape dots in the OLD value so sed matches it literally, not as a wildcard
OLD_RE="$(printf '%s' "$OLD" | sed 's/[.]/\./g')"

FILES=(
  # functional domain values:
  "proxy/overlays/${ENV}.tfvars"
  "sso/overlays/${ENV}.tfvars"
  "frontend/overlays/${ENV}.tfvars"
  "monitoring/overlays/${ENV}.tfvars"
  # comment-only mentions (no functional value, kept in sync so nothing reads stale):
  "ads-server/overlays/${ENV}.tfvars"
  "load_balancer/overlays/${ENV}.tfvars"
)

echo ">> ${DRY:+[dry-run] }env '$ENV': $OLD  ->  $NEW"
total=0
for f in "${FILES[@]}"; do
  [[ -f "$f" ]] || { echo "   (skip $f — not present)"; continue; }
  n="$(grep -c "$OLD_RE" "$f" 2>/dev/null || true)"; n="${n:-0}"
  [[ "$n" -gt 0 ]] || continue
  echo "   $f  ($n occurrence(s))"
  if [[ "$DRY" == true ]]; then
    grep -n "$OLD_RE" "$f" | sed 's/^/      /'
  else
    sed -i "s/$OLD_RE/$NEW/g" "$f"
  fi
  total=$((total + n))
done
echo ">> ${DRY:+would replace }${DRY:+}$([[ "$DRY" == true ]] || echo "replaced ")$total occurrence(s)."

[[ "$DRY" == true ]] && exit 0

cat <<EOF

Review, then redeploy IN ORDER (see proxy/README.md cutover runbook):
  git diff -- ${FILES[*]}
  1. DNS: point  $NEW  (A record) at the LB public IP
  2. (cd sso           && ./deploy-sso.sh $ENV)      # new KC_HOSTNAME/issuer
  3. (cd proxy         && ./deploy-caddy.sh $ENV)    # Caddy re-requests a cert for $NEW
  4. (cd load_balancer && ./deploy.sh $ENV apply)    # :80/:443 -> Caddy (frees the cert challenge)
  5. verify: curl -s https://$NEW/auth/realms/customer360/.well-known/openid-configuration
  6. (cd sso && python3 bootstrap-realm.py)          # redirect URIs -> https://$NEW/*   (needs KC_URL/creds env)
     (cd monitoring && ./deploy-monitoring.sh $ENV)
     (cd server     && ./deploy-api.sh $ENV)
     (cd frontend   && ./deploy-frontend.sh $ENV)

Note: this updated the live overlay config only. Docs (README/diagram) and the
point-in-time proxy/cutover-*.patch are illustrative and left untouched.
EOF
