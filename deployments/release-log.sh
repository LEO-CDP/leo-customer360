#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# release-log.sh — monitor the release ledger (GitHub Deployments API) per env.
#
#   ./release-log.sh                 # recent history for uat + prod
#   ./release-log.sh uat             # recent history for uat only
#   ./release-log.sh prod 50         # prod, last 50
#   ./release-log.sh --current       # latest SUCCESS per (env, service) = what's live now
#   ./release-log.sh --current uat   # what's live in uat, per service
#
# Reads what lib/record_deploy.sh writes. Auth: your `gh auth` (locally) or
# GH_TOKEN (CI). Repo auto-detected, override with GITHUB_REPOSITORY.
# ---------------------------------------------------------------------------
set -euo pipefail
command -v gh      >/dev/null 2>&1 || { echo "release-log: gh not found"      >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || { echo "release-log: python3 not found" >&2; exit 1; }

CURRENT=0; ENVQ=""; LIMIT=30
for a in "$@"; do
  case "$a" in
    -h|--help)        sed -n '2,14p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    --current|--live) CURRENT=1 ;;
    uat|prod)         ENVQ="$a" ;;
    *) if [[ "$a" =~ ^[0-9]+$ ]]; then LIMIT="$a"; else echo "release-log: ignoring arg '$a'" >&2; fi ;;
  esac
done

REPO="${GITHUB_REPOSITORY:-$(gh repo view --json nameWithOwner -q .nameWithOwner 2>/dev/null || echo 'LEO-CDP/leo-customer360')}"
ENVS="${ENVQ:-uat prod}"

python3 - "$REPO" "$LIMIT" "$CURRENT" $ENVS <<'PY'
import json, subprocess, sys
repo, limit, current = sys.argv[1], int(sys.argv[2]), sys.argv[3] == "1"
envs = sys.argv[4:]

def gh(path):
    r = subprocess.run(["gh", "api", path], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        return None

def payload(d):
    p = d.get("payload")
    if isinstance(p, str):
        try: p = json.loads(p)
        except Exception: p = {}
    return p or {}

rows = []
for env in envs:
    deps = gh(f"repos/{repo}/deployments?environment={env}&per_page={limit}") or []
    for d in deps:
        p = payload(d)
        sts = gh(f"repos/{repo}/deployments/{d['id']}/statuses?per_page=1") or []
        rows.append({
            "when": (d.get("created_at") or "")[:19].replace("T", " "),
            "env": env,
            "service": p.get("service") or "?",
            "tag": p.get("tag") or (d.get("ref") or "")[:12],
            "actor": (d.get("creator") or {}).get("login") or p.get("actor") or "?",
            "src": p.get("source") or "?",
            "status": (sts[0]["state"] if sts else "?"),
        })

if current:
    seen, keep = set(), []
    for r in rows:                       # API returns newest-first
        if r["status"] != "success":
            continue
        k = (r["env"], r["service"])
        if k in seen:
            continue
        seen.add(k); keep.append(r)
    rows = sorted(keep, key=lambda r: (r["env"], r["service"]))
    title = f"CURRENT — latest success per service · {repo}"
else:
    title = f"RELEASE HISTORY · {repo}"

cols = ("when", "env", "service", "tag", "actor", "src", "status")
print(title)
if not rows:
    print("(no deployments found — nothing recorded yet for %s)" % " / ".join(envs)); sys.exit(0)
w = {c: max(len(c.upper()), max(len(str(r[c])) for r in rows)) for c in cols}
line = lambda vals: "  ".join(str(v).ljust(w[c]) for c, v in zip(cols, vals))
print(line(c.upper() for c in cols))
print("-" * (sum(w.values()) + 2 * (len(cols) - 1)))
for r in rows:
    print(line(r[c] for c in cols))
PY
