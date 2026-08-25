#!/usr/bin/env bash
# ============================================================================
# deploy-all.sh — one-shot, step-by-step deploy of the whole Customer 360 stack
# ============================================================================
# Runs every per-module deploy script in the correct DEPENDENCY ORDER for an
# environment overlay. Each module keeps its own script (they are the source of
# truth); this is a thin, re-runnable ORCHESTRATOR on top of them.
#
#   ./deploy-all.sh <uat|prod>                 # apply everything, in order (default)
#   ./deploy-all.sh <uat|prod> apply           # same as above (explicit)
#   ./deploy-all.sh <uat|prod> plan            # terraform plan for the IaC steps only
#   ./deploy-all.sh <uat|prod> destroy         # tear everything down (REVERSE order)
#
# Useful flags (any order, after env/action):
#   --list                 print the ordered steps (with phases) and exit
#   --from <step>          start at <step>, skip everything before it (resume)
#   --only <a,b,c>         run ONLY these steps (comma-separated), keep order
#   --skip <a,b,c>         run everything EXCEPT these steps
#   --with <a,b,c>         also run these OPTIONAL steps (e.g. seed)
#   --keep-going           don't stop on the first failure; report at the end
#   --dry-run              print the commands that WOULD run, execute nothing
#   -y | --yes             don't prompt for confirmation before executing
#
# ORDER (apply). Phases group steps by what they depend on:
#   1 infra (Terraform) : storage · postgres · server
#   2 db bootstrap      : db-schema           (needs server as bastion + DB up)
#   3 data-plane        : cache · sso · backend
#   4 front door        : load-balancer · proxy   (Caddy needs LB :80/:443 -> box)
#   5 sso realm + apps  : sso-realm · api · frontend · ads · monitoring
#   6 demo data (opt.)  : seed                 (opt-in: --with seed / --only seed)
#
# The "sso-realm" step (bootstrap-realm.py) and everything that needs the PUBLIC
# HTTPS entry point (api SSO mode, monitoring's oauth2 gate) require that DNS for
# caddy_domain points at the load balancer and Caddy has issued its certificate.
# On a first bring-up before DNS is live, run the infra first, point DNS, then
# resume:  ./deploy-all.sh <env> --from proxy
# (deploy-api falls back to SSO_LOGIN=false until the realm exists, so it is safe
# to run api early and re-run it after sso-realm to switch SSO on.)
#
# Every underlying step is idempotent / converging, so re-running deploy-all.sh
# is safe — Terraform no-ops when there is no drift; the container steps rebuild
# and replace. Secrets/creds live in each module's own .env / terraform.tfvars.
set -uo pipefail
cd "$(dirname "$0")"
ROOT="$(pwd)"

# ---------------------------------------------------------------- step registry
# Ordered list of step ids. PHASE/TITLE give the --list view its structure.
STEPS=(storage postgres server db-schema cache sso backend load-balancer proxy sso-realm api frontend ads tracking monitoring seed)

# Steps NOT run by default (must be named via --with / --only / --from).
OPTIONAL="seed"

phase_of() { case "$1" in
  storage|postgres|server) echo "1 · infrastructure (Terraform)";;
  db-schema)               echo "2 · database bootstrap";;
  cache|sso|backend)       echo "3 · data-plane containers";;
  load-balancer|proxy)     echo "4 · front door (LB + Caddy)";;
  sso-realm|api|frontend|ads|tracking|monitoring) echo "5 · SSO realm + applications";;
  seed)                    echo "6 · demo data (optional)";;
esac; }

title_of() { case "$1" in
  storage)       echo "Object storage buckets (vStorage / S3)";;
  postgres)      echo "Managed PostgreSQL vDB (customer360 + db_keycloak)";;
  server)        echo "vServers / VMs (api box, backend box)";;
  db-schema)     echo "SQL bootstrap: extensions, keycloak db, app schema";;
  cache)         echo "Redis (uat: container on api box; prod: MemStore)";;
  sso)           echo "Keycloak (SSO / OIDC) container";;
  backend)       echo "backend-system (Dagster orchestrator)";;
  load-balancer) echo "L4 NLB fronting Caddy / dagster / dashboards";;
  proxy)         echo "Caddy reverse proxy (TLS + path routing) — cutover";;
  sso-realm)     echo "Keycloak realm + confidential client (bootstrap-realm.py)";;
  api)           echo "customer360-api (FastAPI)";;
  frontend)      echo "frontend-admin (admin UI)";;
  ads)           echo "ads-server (LEO Ad Server, schema leo_ads)";;
  tracking)      echo "data-tracking-api (event ingestion -> S3, /data)";;
  monitoring)    echo "Portainer + Netdata (+ oauth2-proxy SSO gate)";;
  seed)          echo "CIR demo data seed (~1000 profiles, demo tenant)";;
esac; }

# ---------------------------------------------------------------- small helpers
is_tty() { [ -t 1 ]; }
c() { if is_tty; then printf '\033[%sm' "$1"; fi; }   # color on, only for a terminal
C_RESET="$(c 0)"; C_HEAD="$(c '1;36')"; C_OK="$(c '1;32')"; C_WARN="$(c '1;33')"; C_ERR="$(c '1;31')"; C_DIM="$(c '2')"

banner() { printf '\n%s========================================================================%s\n' "$C_HEAD" "$C_RESET"
           printf '%s>>> %s%s\n' "$C_HEAD" "$*" "$C_RESET"
           printf '%s========================================================================%s\n' "$C_HEAD" "$C_RESET"; }
info()  { printf '%s   %s%s\n' "$C_DIM" "$*" "$C_RESET"; }
die()   { printf '%sERROR:%s %s\n' "$C_ERR" "$C_RESET" "$*" >&2; exit 1; }

in_csv() { case ",$1," in *,"$2",*) return 0;; *) return 1;; esac; }

# Read a tfvars value: quoted-string content (keeps '#'), or a bare token with a
# trailing comment stripped — same convention the module scripts use.
tfval() {
  local line; line="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" 2>/dev/null | head -1)"
  case "$line" in
    *\"*\"*) line="${line#*\"}"; printf '%s' "${line%%\"*}" ;;
    *) line="${line#*=}"; line="${line%%#*}"; printf '%s' "$(printf '%s' "$line" | tr -d '[:space:]')" ;;
  esac
}

# Run a command, echoing it first; honour --dry-run. Returns the command's exit.
run() {
  printf '%s   $ %s%s\n' "$C_DIM" "$*" "$C_RESET"
  [ "$DRY_RUN" = 1 ] && return 0
  "$@"
}

# ---------------------------------------------------------------- step runners
# tf step: plan | apply | destroy, straight through to the module's deploy.sh.
tf_step() {  # <dir> <script> <action>
  run bash "$ROOT/$1/$2" "$ENV" "$3"
}

# ssh/container step: apply-arg for a normal deploy, "destroy" to remove it.
ssh_step() {  # <dir> <script> <apply-arg> <action>
  case "$4" in
    apply)   run bash "$ROOT/$1/$2" "$ENV" "$3" ;;
    destroy) run bash "$ROOT/$1/$2" "$ENV" destroy ;;
    plan)    info "no plan for '$CUR' (container/ssh step) — skipped" ;;
  esac
}

# ssh step that only takes <env> (no destroy subcommand of its own).
ssh_step_env_only() {  # <dir> <script> <action>
  case "$3" in
    apply)   run bash "$ROOT/$1/$2" "$ENV" ;;
    destroy) info "'$CUR' has no destroy of its own — the 'server' teardown removes its container; skipped" ;;
    plan)    info "no plan for '$CUR' — skipped" ;;
  esac
}

# one-shot step (schema load, seed): apply only.
oneshot_step() {  # <dir> <script> <action>
  case "$3" in
    apply)   run bash "$ROOT/$1/$2" "$ENV" ;;
    *)       info "'$CUR' only runs on 'apply' — skipped for '$3'" ;;
  esac
}

# sso realm bootstrap: idempotently provision the realm + confidential client and
# write KEYCLOAK_CLIENT_SECRET into sso/.env (consumed by deploy-api.sh).
realm_step() {  # <action>
  if [ "$1" != "apply" ]; then info "'sso-realm' only runs on 'apply' — skipped for '$1'"; return 0; fi
  local ovl="$ROOT/sso/overlays/$ENV.tfvars"
  [ -f "$ovl" ] || { info "overlay $ovl not found — skipping realm bootstrap"; return 0; }
  ( cd "$ROOT/sso"
    [ -f .env ] && { set -a; . ./.env; set +a; }
    local realm client kcurl tenant testuser redirects
    realm="$(tfval api_keycloak_realm "$ovl")";      realm="${realm:-customer360}"
    client="$(tfval api_keycloak_client_id "$ovl")"; client="${client:-customer360-api}"
    kcurl="${KC_URL:-$(tfval api_sso_login_url "$ovl")}"
    tenant="${TENANT_ID:-11111111-1111-1111-1111-111111111111}"
    testuser="${TEST_USER:-c360admin}"
    redirects="${REDIRECT_URIS:-$(tfval sso_redirect_uris "$ovl")}"
    case "$redirects" in ""|"*") die "set sso_redirect_uris in $ovl to an explicit, non-wildcard redirect URI list — refusing to register '*'";; esac
    [ -n "$kcurl" ] || die "could not determine KC_URL — set KC_URL=<public keycloak url> or api_sso_login_url in $ovl"
    # bootstrap-realm.py needs BOTH the KC admin password (to log in) AND the test-user
    # password (KC_TEST_USER_PASSWORD, to set c360admin's password). If either is absent, SKIP
    # this step rather than failing the whole deploy — the realm is idempotent and already
    # provisioned, and CD now runs on every merge to main, so a missing/rotated secret must not
    # break unrelated app deploys. `exit 0` here exits only the subshell, so the deploy continues.
    if [ -z "${KEYCLOAK_ADMIN_PASSWORD:-}" ] || [ -z "${KC_TEST_USER_PASSWORD:-}" ]; then
      info "sso-realm SKIPPED — set KEYCLOAK_ADMIN_PASSWORD + KC_TEST_USER_PASSWORD in sso/.env (CI: repo/env secrets) to (re)provision the realm."
      exit 0
    fi
    info "KC_URL=$kcurl  realm=$realm  client=$client  tenant=$tenant"
    if [ "$DRY_RUN" = 1 ]; then printf '%s   $ KC_URL=%s REALM=%s CLIENT_ID=%s ... python3 bootstrap-realm.py%s\n' "$C_DIM" "$kcurl" "$realm" "$client" "$C_RESET"; return 0; fi
    KC_URL="$kcurl" REALM="$realm" CLIENT_ID="$client" TENANT_ID="$tenant" \
      TEST_USER="$testuser" REDIRECT_URIS="$redirects" python3 bootstrap-realm.py
  )
}

# Dispatch one step id to its runner for the resolved action.
run_one() {  # <id> <action>
  CUR="$1"
  case "$1" in
    storage)       tf_step storage        deploy.sh          "$2" ;;
    postgres)      tf_step postgres        deploy.sh          "$2" ;;
    server)        tf_step server          deploy.sh          "$2" ;;
    load-balancer) tf_step load_balancer   deploy.sh          "$2" ;;
    db-schema)     oneshot_step postgres   run-sql.sh         "$2" ;;
    cache)         ssh_step cache          deploy.sh   apply  "$2" ;;
    sso)           ssh_step sso            deploy-sso.sh deploy "$2" ;;
    proxy)         ssh_step proxy          deploy-caddy.sh deploy "$2" ;;
    frontend)      ssh_step frontend       deploy-frontend.sh deploy "$2" ;;
    ads)           ssh_step ads-server     deploy-ads.sh deploy "$2" ;;
    tracking)      ssh_step_env_only server deploy-tracking.sh "$2" ;;
    monitoring)    ssh_step monitoring     deploy-monitoring.sh deploy "$2" ;;
    backend)       ssh_step_env_only server deploy-backend.sh "$2" ;;
    api)           ssh_step_env_only server deploy-api.sh     "$2" ;;
    sso-realm)     realm_step "$2" ;;
    seed)          oneshot_step server     seed_data.sh       "$2" ;;
    *)             die "unknown step '$1'" ;;
  esac
}

# ---------------------------------------------------------------- arg parsing
ENV=""; ACTION="apply"; DRY_RUN=0; ASSUME_YES=0; KEEP_GOING=0; DO_LIST=0
FROM=""; ONLY=""; SKIP=""; WITH=""
OK_STEPS=(); FAIL_STEPS=()   # init so `set -u` doesn't trip on the summary when nothing failed

usage() { sed -n '2,40p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    uat|prod)            ENV="$1" ;;
    plan|apply|destroy)  ACTION="$1" ;;
    --list)              DO_LIST=1 ;;
    --from)              FROM="${2:?}"; shift ;;
    --only)              ONLY="${2:?}"; shift ;;
    --skip)              SKIP="${2:?}"; shift ;;
    --with)              WITH="${2:?}"; shift ;;
    --from=*)            FROM="${1#*=}" ;;
    --only=*)            ONLY="${1#*=}" ;;
    --skip=*)            SKIP="${1#*=}" ;;
    --with=*)            WITH="${1#*=}" ;;
    --keep-going)        KEEP_GOING=1 ;;
    --dry-run)           DRY_RUN=1 ;;
    -y|--yes)            ASSUME_YES=1 ;;
    -h|--help)           usage 0 ;;
    *)                   die "unknown argument: '$1' (see --help)";;
  esac
  shift
done

# --list works without an env; everything else needs one.
if [ "$DO_LIST" != 1 ]; then
  case "$ENV" in uat|prod) ;; *) die "environment required: ./deploy-all.sh <uat|prod> [plan|apply|destroy] [flags] (see --help)";; esac
fi

# ---------------------------------------------------------------- build the run list
# Start from the full order (minus optional steps unless requested), then apply
# --only / --from / --skip. Order is always preserved from STEPS.
selected=()
for s in "${STEPS[@]}"; do
  if [ -n "$ONLY" ]; then
    in_csv "$ONLY" "$s" && selected+=("$s")
    continue
  fi
  # default set = all non-optional steps, plus any named in --with
  if in_csv "$OPTIONAL" "$s"; then
    in_csv "$WITH" "$s" && selected+=("$s")
  else
    selected+=("$s")
  fi
done

# --from: drop everything before <step>
if [ -n "$FROM" ]; then
  in_csv "$(IFS=,; echo "${selected[*]}")" "$FROM" || die "--from '$FROM' is not in the selected steps"
  trimmed=(); seen=0
  for s in "${selected[@]}"; do [ "$s" = "$FROM" ] && seen=1; [ "$seen" = 1 ] && trimmed+=("$s"); done
  selected=("${trimmed[@]}")
fi

# --skip: remove named steps
if [ -n "$SKIP" ]; then
  kept=(); for s in "${selected[@]}"; do in_csv "$SKIP" "$s" || kept+=("$s"); done
  selected=("${kept[@]}")
fi

# destroy runs in REVERSE dependency order
if [ "$ACTION" = "destroy" ]; then
  rev=(); for ((i=${#selected[@]}-1; i>=0; i--)); do rev+=("${selected[$i]}"); done
  selected=("${rev[@]}")
fi

# ---------------------------------------------------------------- --list and exit
if [ "$DO_LIST" = 1 ]; then
  printf '%sCustomer 360 — deploy-all.sh steps (apply order)%s\n\n' "$C_HEAD" "$C_RESET"
  last_phase=""
  for s in "${STEPS[@]}"; do
    p="$(phase_of "$s")"
    [ "$p" != "$last_phase" ] && { printf '\n%sPhase %s%s\n' "$C_HEAD" "$p" "$C_RESET"; last_phase="$p"; }
    opt=""; in_csv "$OPTIONAL" "$s" && opt="  ${C_WARN}(optional — use --with $s)${C_RESET}"
    printf '   %-14s %s%s\n' "$s" "$(title_of "$s")" "$opt"
  done
  printf '\n%sExamples:%s\n' "$C_HEAD" "$C_RESET"
  printf '   ./deploy-all.sh uat                 # apply all (except optional)\n'
  printf '   ./deploy-all.sh uat --with seed     # apply all + demo data\n'
  printf '   ./deploy-all.sh uat --from proxy    # resume from the Caddy cutover onward\n'
  printf '   ./deploy-all.sh uat --only api,frontend,ads   # just those, in order\n'
  printf '   ./deploy-all.sh uat destroy         # tear down (reverse order)\n'
  exit 0
fi

[ "${#selected[@]}" -gt 0 ] || die "no steps selected (check --only/--from/--skip)"

# ---------------------------------------------------------------- preflight
missing=""
for t in terraform python3 ssh; do command -v "$t" >/dev/null 2>&1 || missing="$missing $t"; done
[ -n "$missing" ] && info "${C_WARN}WARNING${C_RESET}: not on PATH:$missing — some steps will fail without them."

# Align local runs with the REMOTE Terraform state (vStorage) that CI uses:
# load the s3-backend creds into AWS_* and `terraform init` the remote-backend
# modules so a local deploy always reads/writes the SAME state as CI — never a
# stale local terraform.tfstate.d/ copy. Idempotent; safe to run every time.
if [ -f "$ROOT/lib/tfstate.sh" ]; then
  . "$ROOT/lib/tfstate.sh"
  ensure_vstorage_creds "$ROOT" || true
  ensure_remote_init "$ROOT" || true
fi

# ---------------------------------------------------------------- confirm
banner "Customer 360 — $ACTION [$ENV]  ($([ "$DRY_RUN" = 1 ] && echo 'DRY RUN' || echo 'LIVE'))"
printf '   Steps (%d), in order:\n' "${#selected[@]}"
i=1; for s in "${selected[@]}"; do printf '     %2d. %-14s %s\n' "$i" "$s" "$(title_of "$s")"; i=$((i+1)); done
if [ "$ACTION" = "destroy" ]; then printf '\n   %sThis will DESTROY infrastructure for [%s].%s\n' "$C_ERR" "$ENV" "$C_RESET"; fi

if [ "$ASSUME_YES" != 1 ] && [ "$DRY_RUN" != 1 ]; then
  printf '\n   Proceed? [y/N] '
  read -r reply
  case "$reply" in y|Y|yes|YES) ;; *) die "aborted by user.";; esac
fi

# ---------------------------------------------------------------- run
declare -a OK_STEPS FAIL_STEPS
START_TS="$(date +%s 2>/dev/null || echo 0)"
for s in "${selected[@]}"; do
  banner "[$ENV] $s — $(title_of "$s")    ($(phase_of "$s"))"
  if run_one "$s" "$ACTION"; then
    OK_STEPS+=("$s")
    printf '%s   ✓ %s done%s\n' "$C_OK" "$s" "$C_RESET"
  else
    rc=$?
    FAIL_STEPS+=("$s")
    printf '%s   ✗ %s FAILED (exit %s)%s\n' "$C_ERR" "$s" "$rc" "$C_RESET"
    if [ "$KEEP_GOING" != 1 ]; then
      printf '\n%sStopped at "%s". Fix it, then resume with:  ./deploy-all.sh %s %s --from %s%s\n' \
        "$C_ERR" "$s" "$ENV" "$ACTION" "$s" "$C_RESET"
      exit "$rc"
    fi
  fi
done

# ---------------------------------------------------------------- summary
END_TS="$(date +%s 2>/dev/null || echo 0)"
banner "Summary — $ACTION [$ENV]"
printf '%s   OK   (%d): %s%s\n' "$C_OK" "${#OK_STEPS[@]}" "${OK_STEPS[*]:-—}" "$C_RESET"
if [ "${#FAIL_STEPS[@]}" -gt 0 ]; then
  printf '%s   FAIL (%d): %s%s\n' "$C_ERR" "${#FAIL_STEPS[@]}" "${FAIL_STEPS[*]}" "$C_RESET"
fi
[ "$START_TS" -gt 0 ] && info "elapsed: $(( END_TS - START_TS ))s"
[ "${#FAIL_STEPS[@]}" -eq 0 ]
