# Backend System Deployment

This document describes how to deploy the Dagster orchestration layer in
`backend-system/` and when its Docker image must be rebuilt.

## Important count: 9 tasks

The repository currently contains **nine** Dagster task directories:

1. `analytics`
2. `campaign_activation`
3. `data_synch`
4. `email_engine`
5. `identity_resolution`
6. `notification_engine`
7. `personalization`
8. `scoring`
9. `segmentation`

Every task directory contains a `dagster_defs.py` and `requirements.txt`.

Current implementation status:

| Task | Current status |
|---|---|
| `identity_resolution` | Implemented CIR job and optional poll sensor |
| `segmentation` | Implemented segment recomputation job and poll sensor |
| `analytics` | Hourly tracking-log aggregation job and UTC schedule |
| `campaign_activation` | Placeholder Dagster job |
| `data_synch` | Placeholder Dagster job |
| `email_engine` | Placeholder Dagster job |
| `notification_engine` | Placeholder Dagster job |
| `personalization` | Placeholder Dagster job |
| `scoring` | Placeholder Dagster job |

### Workspace registration

`backend-system/workspace.yaml` registers all nine locations:

- `identity_resolution`
- `scoring`
- `segmentation`
- `analytics`
- `data_synch`
- `email_engine`
- `notification_engine`
- `campaign_activation`
- `personalization`

Keep the workspace list and the `backend-system/Dockerfile`
dependency-install loop synchronized. The image must install the
`requirements.txt` file from each of the nine task directories so every code
location can load successfully at runtime.

## Persistence layer (Postgres storage — scaling Phase 0)

Dagster run/event/schedule storage lives in the **shared PostgreSQL** server, not a local SQLite
file. This is configured by `backend-system/dagster.yaml` (the Dagster **instance** config, distinct
from `k8s/base/dagster.yaml` which is the k8s manifest), baked into `DAGSTER_HOME` by the Dockerfile:

- Storage targets a **dedicated `dagster` database** on the same PG server; the connection reuses the
  app's `DB_HOST` / `DB_PORT` / `DB_USER` / `DB_PASSWORD` env (only the DB name is fixed to `dagster`).
- The `dagster` database must **pre-exist** — Dagster auto-creates its *tables* but not the *database*.
  In k8s an init container (`create-dagster-db`) creates it idempotently; on the VM path
  `deployments/server/deploy-backend.sh` creates it before starting the container via a one-shot
  `postgres:16-alpine` psql client (the `DB_USER` needs `CREATEDB`).
- Consequences: the `customer360-dagster` image must be **rebuilt** (Dockerfile added `dagster-postgres`
  and the baked `dagster.yaml`), and the k8s Deployment **no longer uses the `dagster-home` PVC**
  (the single-writer SQLite volume is gone — the prerequisite for running >1 Dagster replica later).
- Compute logs: **in k8s** they go to S3/MinIO via `S3ComputeLogManager`, layered on top of the baked
  config by the `dagster-instance` ConfigMap (`k8s/base/dagster.yaml`); credentials map from the
  MinIO/vStorage secret and a baked `/app/aws-config` (`AWS_CONFIG_FILE`) forces S3 path-style. The
  **single-pod VM path** keeps local compute logs (adequate until it scales). The compute-log bucket
  (`MINIO_BUCKET`, under the `dagster-compute-logs/` prefix) must already exist.

## Migrating existing Dagster history to PostgreSQL (UAT / PROD)

Switching storage to Postgres starts with an **empty** `dagster` database. If the current vServer
already accumulated run history you want to keep, migrate it at cutover.

**Know this first — the VM history is ephemeral.** The VM container runs with **no `DAGSTER_HOME`
volume**, so its SQLite run/event/schedule storage lives only inside the container layer and is
destroyed on every `docker rm`. `deploy-backend.sh` now **auto-backs it up** (`docker cp` →
`/opt/c360/dagster-home-backup-<ts>.tar`) *before* replacing the container — but if you redeploy
without that safeguard, the old history is gone. There is **no supported Dagster command** to move
data between storage backends (`dagster instance migrate` only migrates the *schema* across versions).

**What is actually in there:** operational metadata only — run/event history and sensor/schedule
cursors. The business data (profiles, segments, analytics outputs) lives in the `customer360` DB + S3,
**not** in Dagster storage. So losing it is low-impact; weigh the import risk accordingly.

### Recommended: back up + start fresh
Keep the `*.tar` backup for read-only reference and let Postgres start clean. Simplest and safest.

### If you must import the history
Run the best-effort importer **once**, per env, after the new (Postgres-backed) image is deployed so
Dagster has created its tables:

```bash
# on the VM, in a maintenance window (daemon idle, no runs in flight)
mkdir -p /tmp/old && tar -C /tmp/old -xf /opt/c360/dagster-home-backup-<ts>.tar   # -> /tmp/old/dagster_home
# dry run first — reports row counts, writes nothing
sudo docker run --rm --network host --env-file /opt/c360/backend.env -v /tmp/old:/old \
  customer360-dagster python /app/scripts/migrate_dagster_sqlite_to_postgres.py \
  --old-dagster-home /old/dagster_home --dry-run
# then drop --dry-run to commit
```

The script routes SQLite tables to same-named Postgres tables, copying intersecting columns with
`ON CONFLICT DO NOTHING`. **Caveats:** best-effort and Dagster-version-sensitive — always dry-run and
compare counts, and rehearse on a **staging copy** of the DB before PROD.

### After cutover — sensor cursors reset
Fresh schedule storage means `identity_resolution` / `segmentation` poll-sensor **cursors start empty**.
On the next tick each sensor re-establishes a baseline (it does not replay all history), so expect one
"catch-up" evaluation. Watch the first ticks for unexpected duplicate runs; the CIR/segmentation
sensors are cursor-guarded, so a single re-baseline is normal. Import (above) preserves the cursors and
avoids this.

## Deployment architecture

`backend-system` is currently deployed as **one Docker image** containing all
Dagster code locations:

```text
backend-system/
	Dockerfile
	workspace.yaml
	analytics/
	campaign_activation/
	data_synch/
	email_engine/
	identity_resolution/
	notification_engine/
	personalization/
	scoring/
	segmentation/
```

The image runs:

```text
dagster dev -w workspace.yaml -h 0.0.0.0 -p 3000
```

This starts the Dagster webserver and daemon. The workspace loads one
`dagster_defs.py` per code location. Dagster tracks jobs, sensors, logs, and
run history centrally through the Dagster instance.

The backend system uses one image:

| Image | Source | Runtime role |
|---|---|---|
| `customer360-dagster` | `backend-system/Dockerfile` | Dagster webserver, daemon, and all nine code locations |

Do not confuse a Dagster **code location** with a Docker image. The current
architecture uses one image for all nine backend-system code locations,
including identity resolution. Splitting each task into its own image is a
later scaling decision, not a requirement for Dagster to manage separate jobs.

## When must the image be rebuilt?

### Yes: rebuild `customer360-dagster` when backend code changes

Because the Dockerfile contains `COPY . /app`, the image contains a snapshot of
the entire `backend-system/` directory. A running container will not see source
changes on the host. Rebuild and redeploy `backend-system` after changes to any
of the following:

- Any task's `dagster_defs.py`.
- Any task's Python business-logic package or script.
- Any task's `requirements.txt`.
- `backend-system/workspace.yaml`.
- `backend-system/Dockerfile`.
- `backend-system/requirements-dev.txt` when the dependency is used by the container.
- `backend-system` startup/configuration files copied into the image.

This means a change in **any one of the nine task directories rebuilds the same
`customer360-dagster` image** under the current architecture. The image should then
be rolled out so the Dagster webserver and code-location processes load the new
code.

Changes under `backend-system/identity_resolution/` rebuild the same unified
`customer360-dagster` image. The legacy `worker.py` and `healthcheck.py` files may
remain available for local scripts, but they are not separate production
containers; identity resolution runs as the Dagster job and sensor.

### No image rebuild for configuration-only changes

An image rebuild is normally unnecessary when only runtime configuration
changes, such as:

- Database host, port, name, user, or password.
- `DAGSTER_HOME`.
- `CIR_POLL_INTERVAL_SECONDS`.
- `SEGMENTATION_POLL_INTERVAL_SECONDS`.
- Resource limits, replicas, probes, or Kubernetes Secrets/ConfigMaps.

These changes still require restarting or rolling out the container. Never put
secrets into the Docker image to avoid a rebuild.

## Local Docker deployment

From the repository root:

```bash
docker build \
	-t customer360-dagster:local \
	-f backend-system/Dockerfile \
	backend-system
```

Run it on a Docker network that can reach PostgreSQL:

```bash
docker run -d \
	--name customer360-dagster \
	--restart unless-stopped \
	--network customer360-network \
	--env-file backend-system/.env \
	-e DAGSTER_HOME=/dagster_home \
	-p 3000:3000 \
	customer360-dagster:local
```

The `.env` file must provide the database settings required by the active
jobs. Do not commit it. For local development with the repository's helper
scripts, use:

```bash
cd backend-system
./start.sh
```

The helper starts Dagster from a local virtual environment instead of Docker.
Open the UI at `http://localhost:3000`.

## Kubernetes deployment

The Kubernetes Dagster Deployment is defined in:

```text
k8s/base/dagster.yaml
```

It exposes port `3000` and stores Dagster state under the `dagster-home` PVC.
The local kind overlay uses locally loaded images:

```bash
cd k8s
./scripts/build-load.sh
kubectl apply -k overlays/local
kubectl -n customer360 rollout status deployment/dagster
```

For a code change, rebuild and load the image before restarting the Deployment:

```bash
docker build -t customer360-dagster:local -f ../backend-system/Dockerfile ../backend-system
kind load docker-image customer360-dagster:local --name customer360
kubectl -n customer360 rollout restart deployment/dagster
kubectl -n customer360 rollout status deployment/dagster
```

The VKS overlay is intended to pull a registry image. Replace its placeholder
registry reference with the promoted image digest or release tag before
applying it. Do not use `latest` for production rollback or auditability.

## VM deployment

The existing VM deployment script is:

```text
deployments/server/deploy-backend.sh
```

It deploys the `backend-system` service to the backend VM. In normal CD mode it
pulls the image from GHCR; with `BUILD_LOCAL=1` it ships source and builds on
the VM as an emergency fallback.

Normal deployment:

```bash
cd deployments
bash deploy-all.sh uat --only backend -y
```

The container runs with host networking and exposes Dagster on port `3000`.
The Dagster UI should be exposed only through the intended private network,
SSH tunnel, load balancer, or authenticated proxy.

Emergency local build on the target VM:

```bash
BUILD_LOCAL=1 bash deployments/server/deploy-backend.sh uat
```

Use this only when GHCR is unavailable or while recovering the registry
pipeline. The normal path must build in CI and pull the resulting artifact.

## CI/CD rebuild policy

The CI workflow should treat `backend-system/**` as one image build scope:

```text
backend-system/**
	-> test all available backend-system checks
	-> build customer360-dagster image
```

A backend-system change must not be handled as an image-free Dagster config
change. The container has no live source mount in UAT or production.

Recommended triggers:

| Changed path | Build | Deploy/restart |
|---|---|---|
| Any of the nine task directories | `customer360-dagster` | Dagster Deployment/container |
| `identity_resolution/**` | `customer360-dagster` | Dagster identity-resolution job and sensor |
| `workspace.yaml` | `customer360-dagster` | Dagster |
| `backend-system/Dockerfile` or dependency files | `customer360-dagster` | Dagster |
| Runtime ConfigMap/Secret only | None | Restart affected workload |
| `k8s/base/dagster.yaml` or overlay only | None unless image name changes | Apply manifests and rollout |

Recommended image tags:

- `sha-<full-git-sha>` for every CI build.
- `vX.Y.Z` for release images.
- `latest` only as a UAT convenience tag.
- Production deployments pinned to the registry digest recorded in the release
	manifest.

## CI validation requirements

For every `customer360-dagster` image build:

1. Install the dependencies for all nine registered locations.
2. Import every `dagster_defs.py` and verify its `defs` object loads.
3. Render or validate `workspace.yaml`.
4. Run the identity-resolution and segmentation test suites.
5. Execute placeholder jobs in-process and verify successful Dagster runs.
6. Start the image and verify port `3000` becomes ready.
7. Verify the Dagster UI/GraphQL endpoint can be reached by the API container.
8. Generate an SBOM and scan the image before publishing it.

The CI job should publish `customer360-dagster` only after these checks pass. A
release tag should publish the complete unified backend-system image.

## Operational checks

After deployment:

```bash
kubectl -n customer360 get pods
kubectl -n customer360 logs deploy/dagster --tail=200
kubectl -n customer360 rollout status deployment/dagster
```

For a VM deployment:

```bash
sudo docker ps --filter name=backend-system
sudo docker logs --tail=200 backend-system
curl -fsS http://127.0.0.1:3000/server_info
```

Confirm that:

- All nine expected code locations appear in the Dagster UI after the
	`workspace.yaml` registration fix.
- The identity-resolution and segmentation jobs load successfully.
- The identity-resolution and segmentation sensors are enabled by default and
	managed by the Dagster daemon.
- `DAGSTER_HOME` is backed by persistent storage outside disposable containers.
- No demo seed Job is enabled in production.

## Future split-image architecture

Create one image per task only when a task needs independent release cadence,
scaling, dependencies, or ownership. That design would require:

1. A Dockerfile and image for each task.
2. A separate code server or Dagster deployment configuration per image.
3. Independent CI tests, tags, vulnerability scans, and rollouts.
4. Explicit dependency and sensor ownership to prevent duplicate runs.
5. A release manifest that records all task image digests.

Until those requirements exist, one `backend-system` image is simpler and
matches the current Dockerfile, workspace, VM deployment, and Kubernetes
manifest.
