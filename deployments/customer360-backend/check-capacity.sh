#!/usr/bin/env bash
# Can the VKS cluster host Dagster at vServer parity? Read-only. Exits 1 when it cannot.
#   KUBECONFIG=../kubeconfig-vks-uat.yaml ./check-capacity.sh <uat|prod>
# Called by deploy-backend-vks.sh before anything is applied.
#
# Two questions, both answered per node (a pod must fit on ONE node):
#   1. PARITY: does any node have at least the allocatable CPU/memory the vServer backend box
#      gives Dagster?  (dagster_min_node_cpu_m / dagster_min_node_mem_mi in overlays/<env>.tfvars)
#      The vServer box is s-general-4x8; it was raised from 2x4 because code servers died and
#      run workers OOM'd (../server/overlays/uat.tfvars).
#   2. FIT: after the pods already scheduled there, is the node's free REQUESTABLE room >= the
#      Dagster pod's requests from k8s/overlays/<env>?
set -euo pipefail
cd "$(dirname "$0")"
ENV="${1:?Usage: ./check-capacity.sh <uat|prod>}"
. ../lib/tfvars.sh
ovl="overlays/$ENV.tfvars"
MIN_CPU="$(tfval dagster_min_node_cpu_m "$ovl")"; MIN_CPU="${MIN_CPU:-3500}"
MIN_MEM="$(tfval dagster_min_node_mem_mi "$ovl")"; MIN_MEM="${MIN_MEM:-6000}"
NS="${BACKEND_NAMESPACE:-customer360}"
RENDERED="$(kubectl kustomize "k8s/overlays/$ENV")"
NODES="$(kubectl get nodes -o json)"; PODS="$(kubectl get pods -A -o json)"
python3 - "$MIN_CPU" "$MIN_MEM" <<'PY' <(printf '%s' "$RENDERED") <(printf '%s' "$NODES") <(printf '%s' "$PODS")
import json, re, sys
min_cpu, min_mem = int(sys.argv[1]), int(sys.argv[2])
rendered, nodes, pods = (open(p).read() for p in sys.argv[3:6])
def cpu(v):  v = str(v); return int(v[:-1]) if v.endswith("m") else int(float(v) * 1000)
def mem(v):
    v = str(v); u = {"Ki": 1/1024, "Mi": 1, "Gi": 1024, "Ti": 1024*1024}
    for k, f in u.items():
        if v.endswith(k): return int(float(v[:-2]) * f)
    return int(int(v) / 1024 / 1024)
m = re.search(r"requests:\s*\n\s+cpu: (\S+)\s*\n\s+memory: (\S+)", rendered)
need_cpu, need_mem = cpu(m.group(1).strip('"')), mem(m.group(2).strip('"'))
used = {}
for p in json.loads(pods)["items"]:
    n = p["spec"].get("nodeName")
    if not n or p["status"].get("phase") in ("Succeeded", "Failed"): continue
    for c in p["spec"]["containers"]:
        r = c.get("resources", {}).get("requests", {})
        u = used.setdefault(n, [0, 0]); u[0] += cpu(r.get("cpu", 0)); u[1] += mem(r.get("memory", "0"))
print(f"Dagster pod requests: {need_cpu}m CPU, {need_mem}Mi memory;  vServer-parity node floor: {min_cpu}m / {min_mem}Mi allocatable")
print(f"{'node':40} {'alloc cpu':>9} {'alloc mem':>10} {'free cpu':>9} {'free mem':>9}  parity  fit")
ok = False
for n in json.loads(nodes)["items"]:
    a = n["status"]["allocatable"]; ac, am = cpu(a["cpu"]), mem(a["memory"]); uc, um = used.get(n["metadata"]["name"], [0, 0])
    parity = ac >= min_cpu and am >= min_mem; fit = ac - uc >= need_cpu and am - um >= need_mem
    print(f"{n['metadata']['name']:40} {ac:>8}m {am:>8}Mi {ac-uc:>8}m {am-um:>7}Mi  {'yes' if parity else 'NO ':6}  {'yes' if fit else 'NO'}")
    ok = ok or (parity and fit)
if not ok:
    print("\nRESULT: NO node can host Dagster at vServer parity. Add a node group on s-general-4x8 (or larger)"
          " — see README 'Capacity'. Not deploying.")
sys.exit(0 if ok else 1)
PY
