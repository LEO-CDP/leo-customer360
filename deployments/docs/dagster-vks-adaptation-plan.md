# Dagster → VKS (VNG Cloud Kubernetes Service) Adaptation Plan

> **Status:** plan · **Branch:** `feat/dagster-vks` · **Date:** 2026-10-13
> **Builds on:** [`dagster-scaling-analysis.md`](./dagster-scaling-analysis.md) (topology, phases),
> [`vks-migration-technical-analysis.md`](./vks-migration-technical-analysis.md) (platform),
> [`vks-event-api-migration-runbook.md`](./vks-event-api-migration-runbook.md) (VKS gotchas already paid for)

## 1. Where we are

| Piece | State |
|---|---|
| `k8s/base/dagster.yaml` | One Deployment, `dagster dev` (webserver + daemon + 9 code locations in one pod), `Recreate`, no PVC, image `customer360-dagster:local`. |
| `k8s/overlays/vks` | Rewrites the image to a registry, points `c360-config` at managed PG/Redis/Kafka/vStorage, adds Ingress. Dagster inherits base **unchanged**. |
| Instance config | Rendered at start by `customer360-backend/scripts/render_dagster_instance.py`: PG storage, optional S3 compute logs, `QueuedRunCoordinator`, run monitoring. No run launcher (runs are in-process subprocesses of the pod). |
| API → Dagster | In-cluster GraphQL `dagster:3000` (`DAGSTER_GRAPHQL_HOST/PORT`). Works as-is on VKS. |

So "adapting Dagster to VKS" is two jobs: **(A) make the existing single pod run correctly against
managed services** (small, do first), then **(B) use the cluster for what it is good at** — scale
and isolate runs (the scaling-analysis Phases 1–3).

## 2. Defects to fix before anything runs on VKS

These are found by reading the code; none has been run on a VKS cluster yet.

1. **S3 env-name mismatch.** `S3_BLOCK` reads `{ env: S3_ENDPOINT_URL }`, but the k8s ConfigMap
   (`base/config.yaml`, `overlays/vks/patch-config.yaml`) only defines `S3_ENDPOINT`. The probe
   (`s3_ready`) accepts `S3_ENDPOINT`, so it can pass and then Dagster fails resolving the unset
   env var. Fix in the renderer: write the resolved endpoint literally (or fall back to
   `S3_ENDPOINT`), and add a unit test. Same fix helps every non-Docker environment.
2. **Renderer is fail-closed, manifests say fail-open.** The renderer now exits 1 if PG is
   unreachable (and if S3 is down while `DAGSTER_REQUIRE_S3=true`), but the header comment in
   `k8s/base/dagster.yaml` still describes SQLite/local fallback, and `k8s/README.md` still says
   `DAGSTER_HOME` is on a PVC. Update both. On VKS the pod will CrashLoop until PG is right — that
   is intended; document it.
3. **Dedicated `dagster` DB on managed vDB.** The renderer tries `CREATE DATABASE` best-effort; a
   managed-PG app user usually lacks that privilege. Pre-create `dagster` in terraform (or the
   existing db-init Job pattern used for `db_keycloak`) rather than relying on the renderer.
4. **S3 credentials.** `base/dagster.yaml` maps `AWS_*` from `MINIO_ROOT_USER/PASSWORD`. On VKS
   those must be the **vStorage** keys (`overlays/vks/secret.env`). Set `DAGSTER_REQUIRE_S3=true`
   in the vks patch so a bad key stops the pod instead of silently losing logs.
5. **Registry pull.** `images:` in the vks overlay is `REGISTRY.example`; add the VCR
   `imagePullSecrets` (reuse whatever the event-api runbook settled on) to the Dagster pod spec.
6. **Dagster UI has no auth.** Do **not** put it on the public Ingress as-is. Keep it ClusterIP
   (API reaches it in-cluster) and expose only via `kubectl port-forward` or behind the existing
   oauth2-proxy pattern.

## 3. Phases

Each phase is independently deployable and reversible. Order follows the scaling analysis
(Phase 0 = storage is already done).

### Phase V0 — single pod on VKS (the minimum to be live)
- Fix defects 1–6 above (renderer + vks overlay patch `patch-dagster.yaml`).
- Provision: managed PG with `dagster` DB, vStorage bucket + key, VCR image push
  (`customer360-dagster`), one **general** node group.
- Apply `kubectl apply -k k8s/overlays/vks`.
- **Exit criteria:** pod Ready; PG storage + S3 compute logs lines in `[render-instance]` log;
  API can submit a job via GraphQL and the run's logs appear in the bucket; pod restart keeps run
  history.
- Keep `replicas: 1` + `Recreate` (two `dagster dev` pods = duplicate schedule ticks).

### Phase V1 — split control plane (webserver ×N, daemon ×1)
- Replace the `dagster dev` Deployment with `dagster-webserver` and `dagster-daemon`
  (command per scaling-analysis §5). Daemon: `replicas: 1`, `Recreate`, never HPA.
- Add `dagster instance migrate` as an init container / pre-rollout Job (risk #7).
- Overlay patch for resources; HPA on the webserver only.
- Gate: no duplicate sensor/schedule ticks across a rollout.

### Phase V2 — K8s-native runs (the main VKS payoff)
- Renderer emits `run_launcher: dagster_k8s.K8sRunLauncher` when `DAGSTER_RUN_LAUNCHER=k8s`
  (`job_namespace: customer360`, `env_config_maps: [c360-config]`, `env_secrets: [c360-secrets]`,
  `image_pull_secrets`). Default stays the in-process launcher so local/kind and the Docker
  vServer deploy are untouched.
- Add `dagster-k8s` to the Dockerfile wheel list.
- RBAC: ServiceAccount + Role/RoleBinding permitting `jobs`, `pods`, `pods/log` in the namespace
  for the daemon and webserver.
- Tag the three implemented jobs (`identity_resolution`, `segmentation`, `analytics`) with
  `dagster/pool: compute` and `dagster-k8s/config` resources; keep `backend_job` limit of 1.
- VKS **compute** node group (autoscaling min 0 or 1) with a taint/label (`c360.pool: compute`);
  watch the CPU:RAM node-flavour gotcha in scaling-analysis §13.1.
- Gate: a CIR run launches as a Job pod on the compute pool, finishes, pod is reaped; PG
  connection count stays under the vDB `max_connections` (risk #3 — consider PgBouncer).
- Prerequisite check: with `python_file` workspaces, run pods load code from the same image — fine
  for the single image we ship; confirm with one run before Phase V3.

### Phase V3 — gRPC code locations (only if needed)
- Per scaling-analysis §7. Defer until independent deploy/scale of a code location is a real need;
  six of nine locations are still placeholders.

### Out of scope here
Celery pools (Option A), KEDA, per-location images, `ingestion`/`ai` node pools — revisit when real
jobs land (scaling-analysis Phase 4).

## 4. Files expected to change

| File | Change |
|---|---|
| `customer360-backend/scripts/render_dagster_instance.py` | S3 endpoint fix; optional K8s run launcher block; tests |
| `customer360-backend/Dockerfile` | add `dagster-k8s` |
| `k8s/base/dagster.yaml` | correct stale comments; probes; (V1) split into webserver/daemon |
| `k8s/overlays/vks/` | `patch-dagster.yaml` (REQUIRE_S3, imagePullSecrets, resources, nodeSelector), RBAC, no public Dagster ingress |
| `k8s/README.md` | PVC / fail-open statements, VKS section |
| terraform (separate repo/dir) | `dagster` DB, vStorage bucket/key, node groups |

## 5. Open decisions

1. **Dagster UI exposure** — port-forward only, or behind oauth2-proxy/Keycloak?
2. **Jump straight to V2?** V0→V1 is cheap, but if run isolation is the goal, V1 can be skipped and
   the single `dagster dev` pod keeps webserver+daemon while only the run launcher changes. (Dagster
   supports `K8sRunLauncher` under `dagster dev`; recommended for speed, revisit split when the UI needs HA.)
3. **Compute node group size/flavour and min-nodes** (0 saves cost, adds cold start).
4. **Registry** — confirm VCR repo and the pull-secret name already used by `customer360-event-api`.
5. **Cutover** — Dagster still runs on the vServer UAT/PROD; run both with different `dagster` DBs,
   or cut over in a window and import history with `migrate_dagster_sqlite_to_postgres.py`?
