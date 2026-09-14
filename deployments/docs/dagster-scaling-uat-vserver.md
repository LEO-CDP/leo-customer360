# Customer 360 — Applying the Dagster Scale-Out on UAT (vServer)

> **Status:** applied baseline + forward plan · **Date:** 2026-09-09 · **Scope:** `backend-system/` (Dagster) on **vServer (VM + Docker + SSH)**, **UAT only**
> **Platform:** GreenNode / VNG Cloud vServer — HCM03-1C, `s-general-*` flavor family (not the `s2-general-*` AZ used in the cost tables)
> **Parent analysis:** [`dagster-scaling-analysis.md`](./dagster-scaling-analysis.md) (the full VKS target + PROD topology)
> **Source of truth for today's shape:** [`../server/deploy-backend.sh`](../server/deploy-backend.sh) · [`../../backend-system/deployment.md`](../../backend-system/deployment.md) · [`../../backend-system/scripts/render_dagster_instance.py`](../../backend-system/scripts/render_dagster_instance.py) · [`../server/overlays/uat.tfvars`](../server/overlays/uat.tfvars)

This is the **UAT, vServer-native** application of the parent scaling analysis. The parent
targets **VKS** (Kubernetes node pools, HPA, ephemeral run pods, PROD `5 / 10 / 5`). UAT does
**not** run on VKS yet — it runs on a single VM with Docker over SSH — so the pool model
degrades to the parent's **Option A shape** (fixed containers on sized VMs), and the UAT scale
is a small fraction of PROD. This doc records **what is already deployed**, maps the analysis
phases onto the real UAT box, and gives the concrete forward steps and knobs — all in vServer
terms, with the actual commands from `deploy-backend.sh`.

```yaml
# The UAT overlay from the parent analysis (§3, §13.2) — the ceiling, not day-one:
dagster-webserver: { replicas: 1 }
dagster-daemon:    { replicas: 1 }        # singleton, always
workers:
  ingestion: { replicas: 1, cpu: 2, memory: 4Gi }
  ai:        { replicas: 2, cpu: 4, memory: 8Gi }
  compute:   { replicas: 1, cpu: 4, memory: 16Gi }
```

---

## 1. TL;DR

- **UAT already runs the split control plane on vServer.** [`deploy-backend.sh uat`](../server/deploy-backend.sh)
  launches **two containers** from one image — `backend-system` (the `dagster-webserver` on `:3000`)
  and `backend-system-daemon` (the singleton `dagster-daemon`) — both with `--network host` and the
  same env-file. This is the parent's **Phase 1** (split `dagster dev` → webserver + daemon), done.
- **Storage is already shared and adaptive (Phase 0).** The container entrypoint renders
  `$DAGSTER_HOME/dagster.yaml` at start via [`render_dagster_instance.py`](../../backend-system/scripts/render_dagster_instance.py):
  **PostgreSQL** run/event/schedule storage (dedicated `dagster` DB, best-effort created) and
  **S3 / vStorage** compute logs, each probed and used only if reachable.
- **The run queue is already bounded (part of §6.3).** The renderer always writes a
  `QueuedRunCoordinator` (`max_concurrent_runs`, **default 2**, env-tunable) plus a `run_monitoring`
  reaper so orphaned runs release their slot instead of deadlocking the queue.
- **What UAT does NOT have yet:** worker pools (no Celery, no `ingestion / ai / compute`
  containers), per-pool `tag_concurrency_limits`, and the gRPC-per-code-location split. Runs
  execute **in-process on the daemon box** via the `DefaultRunLauncher` — so all run compute
  lands on the one VM.
- **The box is the whole story on vServer.** UAT is a **single `s-general-2x4` VM (2 vCPU / 4 GB)**
  — recently resized up from `1x2` precisely because in-process run workers were OOM/swapping.
  There are **no node pools, no HPA, no ephemeral pods, no scale-to-zero** — you pay always-on and
  you scale by making the box bigger or by adding whole worker VMs.
- **Recommendation for UAT:** stay on the **single right-sized box** (Stage 1 below) until measured
  queue depth or memory pressure forces it. Only then stand up the `1 / 2 / 1` worker VMs (Stage 2)
  — and even then, UAT almost certainly needs less than the analysis's UAT ceiling.

---

## 2. What "UAT on vServer" changes vs. the VKS analysis

The parent analysis assumes an orchestrator (Kubernetes). UAT has none yet, so four of its
mechanisms are simply unavailable and the pool model collapses to fixed containers:

| Parent (VKS target) | UAT reality (vServer) |
|---|---|
| Node pools per tier (`c360.pool=…`), autoscaler `0→N` | Whole VMs at fixed flavors; **no autoscale, no scale-to-zero** |
| `K8sRunLauncher` + `k8s_job_executor` (Option B, ephemeral) | **Not available** — no K8s API. Runs use the `DefaultRunLauncher` (in-process on the daemon) |
| Webserver HPA (floor 2, ceiling 5) | Single webserver container; scale = bigger box |
| Ephemeral per-run/per-step pods | Run compute executes **on the daemon VM itself** |
| Worker pools = Celery *or* K8s Jobs | **Celery workers on VMs only** (parent's Option A, §6.1) — the sole pool shape here |
| `s2-general` 1:2 flavors, memory-optimized 1:4 option | HCM03-1C offers the **`s-general-*`** family only; the `s2-general-*` rate card is a different (sold-out) AZ — see [`uat.tfvars`](../server/overlays/uat.tfvars) |

> [!IMPORTANT] The daemon box carries the run compute on UAT
> With the `DefaultRunLauncher`, every launched run executes as a subprocess **on the
> `backend-system-daemon` container's VM**. There is no isolation and no per-op scaling — this is
> the parent's §2 baseline behavior. It is why the UAT box was resized `1x2 → 2x4`, and why the
> bounded `max_concurrent_runs` matters: 2 concurrent identity/segmentation/analytics runs on a
> 4 GB box is already near the memory edge. Measure before raising it.

---

## 3. Current UAT baseline (what is actually deployed today)

Grounded in [`deploy-backend.sh`](../server/deploy-backend.sh), [`render_dagster_instance.py`](../../backend-system/scripts/render_dagster_instance.py),
and [`uat.tfvars`](../server/overlays/uat.tfvars):

| Aspect | UAT today |
|---|---|
| Host | **one** `s-general-2x4` VM (2 vCPU / 4 GB, 20 GB SSD), HCM03-1C, floating IP for SSH |
| Process model | **two containers, one image** (`customer360-dagster`): `backend-system` = webserver `:3000`; `backend-system-daemon` = singleton daemon |
| Networking | `--network host` (reaches the **private** customer360 vDB in the same subnet) |
| Run/event/schedule storage | **PostgreSQL** (dedicated `dagster` DB on the managed vDB), adaptive — falls back to local SQLite only if the DB is unreachable |
| Compute logs | **S3 / vStorage** (`S3ComputeLogManager`, path-style, `dagster-compute-logs/` prefix), adaptive — local logs if the bucket doesn't answer |
| Run launcher | `DefaultRunLauncher` — runs execute in-process **on the daemon box** |
| Run queue | `QueuedRunCoordinator`, `max_concurrent_runs=2` (env `DAGSTER_MAX_CONCURRENT_RUNS`) |
| Orphan reaping | `run_monitoring` enabled — `start_timeout=300s`, `poll=60s`, `max_resume_run_attempts=0` |
| Code locations | **9** loaded in-process from `workspace.yaml` (no gRPC split) |
| `DAGSTER_HOME` | ephemeral per container; `deploy-backend.sh` tars a best-effort backup to `/opt/c360/dagster-home-backup-<ts>.tar` before each redeploy |
| Image source | GHCR pull by default; `BUILD_LOCAL=1` builds on the VM (emergency fallback) |
| Log rotation | `--log-opt max-size=10m --log-opt max-file=3` on both containers (can't fill the disk) |

**Mapping onto the parent's phased rollout (§10):**

| Parent phase | UAT status on vServer |
|---|---|
| Phase 0 — storage → Postgres + S3 (adaptive, fail-open) | ✅ **Done** (renderer) |
| Phase 1 — split webserver (N) + daemon (1) | ✅ **Done** (two containers) |
| §6.3 — `QueuedRunCoordinator` (bounded queue) | ✅ **Partial** — global cap only; no per-pool `tag_concurrency_limits` yet |
| Phase 2 — queued execution with pool tags + a real launcher | ⬜ Not on vServer (needs K8s or Celery workers) |
| Phase 3 — gRPC per code location | ⬜ Not started (single in-process workspace) |
| Phase 4 — scale the `ingestion / ai / compute` pools | ⬜ Not started (single box carries everything) |

> [!WARNING] UAT box is under the documented UAT requirement
> [`deployment.md`](../../backend-system/deployment.md) **Mode 1 (UAT, ~1,000 virtual users)** calls
> for **4 vCPU / 8 GB**. The live box is **2 vCPU / 4 GB** (`s-general-2x4`) — the pragmatic resize
> that stopped the OOM/swap, not the full documented target. Treat closing that gap (Stage 1) as the
> next step **before** any worker-VM fan-out. Confirm the flavor is enabled in HCM03-1C before relying
> on it — the tfvars pins zone/image IDs directly because the name lookups hit sold-out zones.

---

## 4. UAT sizing — the ask vs. what UAT needs

The parent's UAT overlay is `webserver 1 / daemon 1 / pools 1·2·1`. On vServer each pool replica is
a **whole VM** (Celery worker, Option A). Applied literally, that is **4 extra worker VMs** on top of
the control-plane box:

| Tier | UAT ask (parent §13.2) | vServer mapping | Blocks¹ |
|---|---|---|---:|
| control plane | webserver 1 + daemon 1 (+ 9 in-process locations) | the **existing** `s-general-2x4` box | 2 |
| ingestion | 1 × 2 vCPU / 4 Gi | 1 VM (`s-general-2x4`) | 2 |
| ai | 2 × 4 vCPU / 8 Gi | 2 VMs (`s-general-4x8` each) | 8 |
| compute | 1 × 4 vCPU / 16 Gi | 1 VM (needs ≥ 16 GB → memory tax on 1:2) | 8 |
| **UAT `1/2/1` always-on total** | — | **5 VMs** | **≈ 20 blocks** |

¹ `1 block = 1 vCPU + 2 GB`. Compute's `4 vCPU / 16 Gi` is 1:4, so on a 1:2 `s-general` flavor you
provision 8 vCPU to get 16 GB — it bills as **8 blocks, not 4** (parent §13.1 memory tax). The
`s-general` family has no published 1:4 flavor to escape it in this AZ.

**Why UAT should not adopt `1/2/1` as day-one:**

- **Only 3 of 9 code locations do real work** (`identity_resolution`, `segmentation`, `analytics`);
  the other six are runnable placeholders. `scoring` and `personalization` — the entire reason for
  the `ai` pool — are placeholders, so 2 always-on `ai` VMs (8 blocks) would idle.
- **No scale-to-zero on vServer.** Every worker VM is a standing bill 24/7. The parent's whole cost
  argument (Option B / KEDA floor) does not exist here — you pay the ceiling continuously.
- **`max_concurrent_runs=2` today.** The queue admits 2 runs at a time; a 4-VM pool fabric to serve
  a depth-2 queue is pure overhead until the cap (and measured depth) rises.

**Recommendation:** keep UAT as a **single right-sized box** (Stage 1). Adopt worker VMs (Stage 2)
only for a pool with a **measured, sustained** queue — and size that pool from the measurement, not
from the `1/2/1` ceiling. This is the parent's own "right-size from measured queue depth" rule (§9,
§13.3) applied to UAT.

### 4.1 Which vServer flavor fits UAT

Because the whole UAT topology (control plane + all 9 in-process locations + the run compute itself)
lives on **one VM**, the "which vServer" question is just: what single flavor holds it. On the
`s-general` 1:2 family available in HCM03-1C:

| Flavor | vCPU / RAM | Fit for UAT | Notes |
|---|---|---|---|
| `s-general-1x2` | 1 / 2 GB | ❌ too small | The original box — run workers OOM/swapped; already abandoned |
| `s-general-2x4` | 2 / 4 GB | ⚠️ minimum, **live today** | Works, but under target; ~2 concurrent runs on 4 GB is at the memory edge |
| **`s-general-4x8`** | **4 / 8 GB** | ✅ **recommended fit** | Matches [`deployment.md`](../../backend-system/deployment.md) **Mode 1** (UAT, ~1,000 VUs); headroom for `max_concurrent_runs` + Polars/identity materialization |
| `s-general-8x16`+ | 8 / 16 GB+ | ❌ overkill | PROD/Mode 2 territory; UAT has 6 of 9 locations as placeholders and no scale-to-zero |

> [!TIP] The fit for UAT is **`s-general-4x8`** (4 vCPU / 8 GB) + a **50 GB** root disk
> That is the documented Mode 1 target. It must be an `s-general` flavor (HCM03-1C offers only that
> family; the `s2-general` rate card is a different, sold-out AZ — §2). Also bump the root disk from
> the current **20 GB → 50 GB** (Mode 1 wants 50 GB SSD with ≥30 % free). The resize is **in-place**
> (0 destroy) and **reboots** the box — same mechanics as the earlier `1x2 → 2x4` change. Apply it in
> Stage 1 (§5).

---

## 5. Phased plan — applying the scale-out on UAT (vServer)

Each stage is independently deployable and reversible. Stages 0–1 are the realistic UAT path;
Stage 2 is on-demand and Stage 3 is the eventual VKS handoff.

### Stage 0 — split control plane + shared storage + bounded queue ✅ *(already deployed)*

Delivered by the current `deploy-backend.sh` + renderer. Nothing to do; documented in §3.

```bash
# Normal redeploy (pulls the CI image from GHCR, re-renders the instance config):
cd deployments
bash deploy-all.sh uat --only backend -y
#   or directly:
bash deployments/server/deploy-backend.sh uat
```

### Stage 1 — right-size the single box to the documented UAT target *(recommended next)*

Bring the control-plane box to `deployment.md` Mode 1 (**4 vCPU / 8 GB**) so in-process run workers
stop competing for 4 GB. This is a one-line overlay change; it is an **in-place** resize (0 destroy)
that **reboots** the box on apply.

```hcl
# deployments/server/overlays/uat.tfvars — server key "1x2" (the backend box)
servers = {
  "1x2" = {
    flavor_name    = "s-general-4x8" # 4 vCPU / 8 GB — UAT Mode 1 target (was s-general-2x4)
    root_disk_size = 50              # was 20 — Mode 1 wants 50 GB SSD, ≥30% free
    name           = "backend"
  }
  # ...
}
```

Both edits are **in-place** terraform changes (0 destroy) that **reboot** the box on apply — the
flavor and the root-disk grow are the same mechanics as the earlier `1x2 → 2x4` resize.

```bash
cd deployments/server
./deploy.sh uat plan     # confirm: in-place resize, 0 to destroy
./deploy.sh uat apply    # reboots the box
bash deploy-backend.sh uat   # containers come back up on the larger box
```

With 8 GB you may cautiously raise the queue cap; measure memory first (§7):

```bash
# on the box, via the container env-file — re-render happens at container start
DAGSTER_MAX_CONCURRENT_RUNS=3   # only after confirming headroom for N concurrent runs
```

### Stage 2 — introduce UAT worker VMs (Celery, Option A) *(on-demand, per pool)*

Only when a pool has a **measured, sustained** queue. On vServer this is the parent's §6.1 Celery
shape: a broker (reuse the existing Redis), a `celery_k8s_job_executor`/`celery_executor`, and one
**worker container pinned to a sized VM per pool replica**. Add per-pool caps to the run coordinator.

```yaml
# $DAGSTER_HOME/dagster.yaml delta (rendered) — UAT per-pool caps
run_coordinator:
  config:
    max_concurrent_runs: 4            # UAT: 1 + 2 + 1
    tag_concurrency_limits:
      - { key: "dagster/pool", value: "ingestion", limit: 1 }
      - { key: "dagster/pool", value: "ai",        limit: 2 }
      - { key: "dagster/pool", value: "compute",   limit: 1 }
```

Add each worker VM to `uat.tfvars` (new server keys) and run one Celery worker container per pool,
e.g. the `ai` worker:

```bash
# on the ai worker VM (2 replicas → 2 VMs), same image, subscribe to the "ai" queue
sudo docker run -d --name dagster-worker-ai --restart unless-stopped \
  --log-opt max-size=10m --log-opt max-file=3 --network host \
  --env-file /opt/c360/backend.env --entrypoint /app/entrypoint.sh \
  customer360-dagster \
  dagster-celery worker start -q ai -A dagster_celery_k8s.app
```

Tag the three implemented jobs onto pools first (`analytics`, `segmentation`, `identity_resolution`
→ **compute**), exactly as the parent §7.1 prescribes. Bringing up `ai`/`ingestion` VMs waits on
`scoring`/`personalization`/ingest locations becoming real.

> Stage 2 adds standing cost with no scale-to-zero (§4). Prefer it only if Stage 1's single box
> can't hold the measured concurrency, and size each pool from the measurement.

### Stage 3 — VKS handoff *(out of scope here)*

The moment UAT moves onto VKS, the pool/HPA/ephemeral-pod model in the parent analysis applies
directly and Stage 2's fixed worker VMs retire. Track that under
[`vks-migration-technical-analysis.md`](./vks-migration-technical-analysis.md).

---

## 6. Config knobs (where UAT actually tunes this)

All rendered by [`render_dagster_instance.py`](../../backend-system/scripts/render_dagster_instance.py)
at container start from the env-file `deploy-backend.sh` writes to `/opt/c360/backend.env`:

| Knob (env var) | Default | Effect |
|---|---|---|
| `DAGSTER_MAX_CONCURRENT_RUNS` | `2` | Global run-queue ceiling (the whole UAT concurrency budget) |
| `DAGSTER_RUN_START_TIMEOUT_SECONDS` | `300` | STARTING-run timeout before the reaper releases the slot |
| `DAGSTER_PG_DB` | `dagster` | Dedicated Dagster database name on the vDB |
| `DAGSTER_REQUIRE_S3` | `false` | If `true`, refuse to start without a reachable S3 bucket (UAT leaves this off; PROD sets it) |
| `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` | from `../postgres` | Postgres storage target (also the app DB host) |
| `S3_ENDPOINT_URL` / `MINIO_BUCKET` / `AWS_*` | from `../storage` | Compute-log bucket; unset → local logs |

Storage backend and the run coordinator are **not baked** — change the env-file and redeploy (the
container re-renders `dagster.yaml` on every start). Per-pool `tag_concurrency_limits` are **not**
rendered today; they arrive with Stage 2 (add them to the renderer alongside `max_concurrent_runs`).

---

## 7. Verification & operational checks (UAT box)

```bash
# SSH tunnel to the UI (never expose :3000 publicly):
ssh -i ~/.ssh/c360-api_ed25519 -L 3000:localhost:3000 leocdp360@<uat-backend-fip>
# then open http://localhost:3000

# On the box:
sudo docker ps --filter name=backend-system          # expect BOTH backend-system + backend-system-daemon
sudo docker logs --tail=100 backend-system-daemon | grep render-instance
curl -fsS http://127.0.0.1:3000/server_info
sudo docker exec backend-system sh -c 'cat /dagster_home/dagster.yaml'
free -h && df -h /                                    # memory headroom + disk (keep ≥30% free)
```

Check immediately after a redeploy:

- **Exactly one** `backend-system-daemon` container is running (two daemons ⇒ duplicate ticks).
- `render-instance` logged `storage: PostgreSQL (shared)` — **not** a SQLite fallback.
- `render-instance` logged `run coordinator: QueuedRunCoordinator (max_concurrent_runs=…)`.
- All 9 code locations load in the UI; the `identity_resolution` and `segmentation` sensors are
  enabled and owned by the one daemon.
- Runs are **draining**, not stuck queued (a stuck queue ⇒ check the reaper / daemon logs).
- Memory is not pegged during a run — the single box carries run compute (§2).

---

## 8. Decisions & risks (UAT-specific)

| # | Item | Call for UAT |
|---|---|---|
| 1 | Box size: keep `2x4` or go `4x8` | **Go `4x8`** (Stage 1) to meet `deployment.md` Mode 1 and stop run-worker OOM/swap |
| 2 | Raise `max_concurrent_runs` above 2 | Only after Stage 1 + a memory measurement under representative load |
| 3 | Stand up `1/2/1` worker VMs now | **No** — 3 of 9 locations are real; `ai` targets are placeholders; no scale-to-zero |
| 4 | Two daemons during a rollout | `deploy-backend.sh` `docker rm -f`s the old daemon before starting the new one; verify only one runs |
| 5 | Losing run history on redeploy | Operational metadata only; best-effort tar backup + importer exist (`deployment.md`); business data is in the vDB + S3 |
| 6 | Flavor availability in HCM03-1C | `s-general-*` only; confirm `s-general-4x8` is enabled before Stage 1 (tfvars pins zone/image IDs deliberately) |
| 7 | S3 required in UAT | Leave `DAGSTER_REQUIRE_S3=false` (UAT tolerates local logs); PROD sets it true |

---

### Appendix — UAT vServer command cheat-sheet

| Action | Command |
|---|---|
| Deploy/redeploy backend to UAT | `cd deployments && bash deploy-all.sh uat --only backend -y` |
| Direct backend deploy | `bash deployments/server/deploy-backend.sh uat` |
| Emergency build-on-VM | `BUILD_LOCAL=1 bash deployments/server/deploy-backend.sh uat` |
| Resize the box | edit `deployments/server/overlays/uat.tfvars` → `cd deployments/server && ./deploy.sh uat apply` |
| Tunnel to the UI | `ssh -i ~/.ssh/c360-api_ed25519 -L 3000:localhost:3000 leocdp360@<fip>` |
| Inspect rendered instance | `sudo docker exec backend-system cat /dagster_home/dagster.yaml` |
| Webserver command (image CMD) | `dagster-webserver -w workspace.yaml -h 0.0.0.0 -p 3000` |
| Daemon command (singleton) | `dagster-daemon run -w workspace.yaml` |
