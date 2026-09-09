# Backend System Deployment

This is the production runbook for the Dagster orchestration layer in
`backend-system/`. It defines the supported topology, required state stores,
release gates, deployment commands, rollback boundaries, and operational checks.

## Production decision

The supported production topology is the root `docker-compose.yml` stack:

| Component | Cardinality | Role |
|---|---:|---|
| `dagster` | 1 or more webservers | GraphQL/UI endpoint and code-location loading |
| `dagster-daemon` | exactly 1 | Schedules, sensors, run queue, and run monitoring |
| `dagster-db-init` | one-shot | Creates the dedicated `dagster` PostgreSQL database |
| PostgreSQL | shared | Durable Dagster run, event, and schedule storage |
| S3-compatible storage | shared | Durable compute logs |

Do not run two daemons. There is no daemon leader election in this deployment,
so two daemon processes can launch duplicate schedule or sensor runs. Do not
run multiple webservers against SQLite or a shared local filesystem.

The VM deployment script and the current Kubernetes manifest still represent the
legacy single-container `dagster dev` topology. They are not equivalent to the
production Compose topology and must not be used for a production rollout until
they are migrated to separate webserver and daemon workloads with shared
PostgreSQL and object storage. This distinction is intentional and is called
out below in the deployment sections.

## Go-live prerequisites

Complete these checks before the first production deployment:

- A dedicated PostgreSQL database named by `DAGSTER_PG_DB` exists, or the
  database init job's `DB_USER` has permission to create databases.
- PostgreSQL accepts connections from both Dagster services using `DB_HOST`,
  `DB_PORT`, `DB_USER`, and `DB_PASSWORD`.
- The S3-compatible endpoint, bucket, region, and credentials are configured.
  The bucket must already exist and the credentials must be able to check and
  write objects. The Dagster renderer performs `head_bucket` before startup.
- `DAGSTER_REQUIRE_S3=true` is explicitly set in the production environment.
  Do not rely on the local-development default in `.env.example`.
- The image is built in CI, tagged with an immutable Git SHA or digest, tested,
  scanned, and promoted. Do not build unreviewed source on the production host.
- The base image reference is pinned to a reviewed digest and the final image
	passes vulnerability scanning. A mutable `python:3.11-slim*` tag is not a
	sufficient production artifact by itself.
- A PostgreSQL backup has completed and its restore procedure has been tested.
- The release has a maintenance window or a documented rollback owner. Dagster
  database migrations can make an older image unsafe to roll back to.

Required production secrets must come from the deployment secret store or a
root-owned file with mode `0600`. Never commit `.env`, print it in logs, or put
credentials in a Dockerfile, image label, command argument, or ticket.

The current Compose file attaches the complete `.env` file to the API and
Dagster containers. Use this only on a trusted host and treat every value in
that file as exposed to those processes. Before operating in a higher-risk
multi-tenant environment, replace the broad `env_file` usage with explicit
allowlisted variables and dedicated secrets for each service.

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

## Persistence layer - PostgreSQL + object storage

Dagster's instance config is rendered at container start, not baked. The
production Compose stack runs `dagster-db-init` first to provision a dedicated
`dagster` database, then `entrypoint.sh` writes `$DAGSTER_HOME/dagster.yaml`:

- **Storage:** shared PostgreSQL is mandatory for run, event, and schedule
  state. If it is unavailable, the webserver and daemon refuse to start rather
  than silently switching to a private SQLite database.
- **Compute logs:** set `DAGSTER_REQUIRE_S3=true` and provision
  `DAGSTER_LOGS_BUCKET` before deployment. The startup probe requires the
  S3-compatible endpoint and bucket to answer `head_bucket`; logs use the
  `dagster-compute-logs/` prefix and path-style addressing where configured.
- **DAGSTER_HOME:** the Compose webserver and singleton daemon do not share a
  filesystem volume. PostgreSQL and S3 are the shared state, so containers can
  be replaced without losing run history or compute logs.
- **Consequences:** rebuild `customer360-dagster` after code or dependency
  changes, and deploy the webserver and daemon together with the same image
  digest and environment contract.

The renderer is deliberately fail-closed. A PostgreSQL outage stops startup;
an S3 outage stops startup when `DAGSTER_REQUIRE_S3=true`. This protects
durability, but it means a storage outage is a service outage and must be
handled as such. Do not change the flag to restore availability without an
incident decision and an explicit acceptance of lost or local-only compute logs.

## Dagster history and database migrations

New deployments start with an empty dedicated `dagster` database. Dagster owns
the schema and applies its instance migrations when the services start. Back up
the database before upgrading the Dagster image and verify the migration in
staging first.

Dagster run history, event history, schedule state, and sensor cursors are
operational metadata. Customer profiles, segments, and analytics outputs live
in the application database and object storage and are not restored by a
Dagster metadata restore.

The legacy VM container may contain SQLite history under `/dagster_home`.
`deploy-backend.sh` attempts to copy that directory to
`/opt/c360/dagster-home-backup-<timestamp>.tar` before replacement. Treat this
as a best-effort forensic backup, not a durable migration. There is no general
Dagster command that converts SQLite run history to PostgreSQL.

If legacy history is required, use the repository importer only after a dry run
on a staging database:

```bash
mkdir -p /tmp/old
tar -C /tmp/old -xf /opt/c360/dagster-home-backup-<timestamp>.tar

sudo docker run --rm --network host \
	--env-file /opt/c360/backend.env \
	-v /tmp/old:/old \
	--entrypoint python customer360-dagster \
	/app/scripts/migrate_dagster_sqlite_to_postgres.py \
	--old-dagster-home /old/dagster_home --dry-run
```

The importer is best-effort and Dagster-version-sensitive. Stop the singleton
daemon, back up the target database, compare row counts, and rehearse the
import on a staging copy before removing `--dry-run`. Fresh schedule storage
resets sensor cursors and can cause one baseline evaluation; monitor the first
identity-resolution and segmentation ticks for unexpected duplicate runs.

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

The image runs two production commands:

```text
dagster-webserver -w workspace.yaml -h 0.0.0.0 -p 3000
dagster-daemon run -w workspace.yaml
```

The webserver is the network-facing process; exactly one daemon owns
schedules, sensors, run monitoring, and the run queue. Both load one
`dagster_defs.py` per code location and use the same PostgreSQL-backed
Dagster instance.

The backend system uses one image:

| Image | Source | Runtime role |
|---|---|---|
| `customer360-dagster` | `backend-system/Dockerfile` | Dagster webserver or singleton daemon, plus all nine code locations |

The image includes identity resolution. Splitting each task into its own image
is a later scaling decision, not a requirement for Dagster to manage separate
jobs.

## Release procedure

Use this order for a normal production release:

1. Review changes under `backend-system/`, the workspace registration, and all
	dependency files.
2. Run the focused tests and Compose validation from the CI checklist.
3. Build one image, scan it, generate its SBOM, and record its immutable image
	digest.
4. Back up the application and Dagster PostgreSQL databases. Confirm the S3
	compute-log bucket is reachable and has sufficient retention and quota.
5. Deploy the same image digest to `dagster` and `dagster-daemon`.
6. Wait for `dagster-db-init`, the webserver health check, and the daemon logs.
7. Verify all code locations, sensors, schedules, and one controlled smoke run.
8. Record the image digest, database migration result, operator, and deployment
	time in the release record.

Example PostgreSQL backups, run from a trusted host with credentials supplied
through the environment or secret manager:

```bash
pg_dump --format=custom --file=customer360-$(date -u +%Y%m%dT%H%M%SZ).dump \
  "$CUSTOMER360_DATABASE_URL"
pg_dump --format=custom --file=dagster-$(date -u +%Y%m%dT%H%M%SZ).dump \
  "$DAGSTER_DATABASE_URL"
```

Do not place database URLs containing passwords in shell history or CI logs.

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

### Supported Compose deployment

The root Compose file is the reference deployment for the current two-process
topology. From the repository root:

```bash
cp .env.example .env
chmod 600 .env
# Edit .env. Set real passwords, DB settings, S3 settings, and:
# DAGSTER_REQUIRE_S3=true

docker compose config --quiet
docker compose build dagster
docker compose up -d \
	postgres redis keycloak-db-init keycloak \
	dagster-db-init dagster dagster-daemon api tracking-api
```

`dagster-daemon` uses the same image built by `docker compose build dagster`.
Do not start a second daemon manually. The database init job must finish with
exit code `0` before either Dagster service starts.

Verify the rollout:

```bash
docker compose ps
docker compose logs --tail=200 dagster-db-init dagster dagster-daemon
curl -fsS http://127.0.0.1:${DAGSTER_UI_PORT:-3000}/server_info
curl -fsS http://127.0.0.1:${C360_API_PORT:-8008}/health
docker compose exec api python -c \
	"import urllib.request; print(urllib.request.urlopen('http://dagster:3000/server_info', timeout=5).status)"
docker compose exec dagster sh -c 'cat /dagster_home/dagster.yaml'
```

Expected conditions:

- `postgres`, `redis`, `keycloak`, `dagster`, and `api` are healthy.
- `dagster-db-init` is `exited (0)`.
- `dagster-daemon` is running and there is exactly one instance.
- `/server_info` and `/health` return successfully.
- The generated `dagster.yaml` contains PostgreSQL storage and, when
	`DAGSTER_REQUIRE_S3=true`, an S3 compute-log manager.
- All nine code locations load in the Dagster UI.

For a local smoke test without an S3 service, set
`DAGSTER_REQUIRE_S3=false` explicitly. That validates PostgreSQL-backed Dagster
metadata but does not validate durable compute logs and is not a production
configuration.

Stop the stack without deleting data:

```bash
docker compose down
```

Never use `docker compose down -v` in production. It deletes the PostgreSQL and
Redis volumes.

### Compose rollback

1. Stop new scheduling and confirm no critical run is in flight.
2. Record the current image digest and service logs.
3. Restore the PostgreSQL backup if the failed release changed Dagster schema
	 or application data.
4. Pin both `dagster` and `dagster-daemon` to the previous tested image digest.
5. Start the database, `dagster-db-init`, webserver, and exactly one daemon.
6. Verify `/server_info`, code locations, sensors, and a controlled smoke run.

Do not roll back only the webserver or only the daemon. They must run the same
image and compatible Dagster schema. If a migration has already been applied,
the previous image may not be able to read the database.

### Development-only single-container run

The following command is for local debugging only. It runs one Dagster process
and is not a production deployment because it does not provide the separate
singleton daemon contract.

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

The current `k8s/base/dagster.yaml` is a legacy single Deployment that runs
`dagster dev` and uses a local Dagster home. It is suitable for local kind
experiments only. It is not a production Kubernetes manifest.

A production Kubernetes migration must provide, at minimum:

1. A webserver Deployment and Service. The webserver may scale horizontally.
2. A daemon Deployment with exactly one replica and a `Recreate` strategy.
3. Shared PostgreSQL storage for Dagster metadata.
4. Shared S3-compatible compute logs.
5. Separate ConfigMap and Secret inputs, with no secrets in the image.
6. Readiness and liveness probes, resource requests, a PodDisruptionBudget
	where appropriate, and an image digest rather than `latest`.

Do not increase replicas on the current manifest. That would create competing
daemons and SQLite writers.

The Kubernetes Dagster Deployment is defined in:

```text
k8s/base/dagster.yaml
```

It exposes port `3000`. The local kind overlay uses locally loaded images:

```bash
cd k8s
./scripts/build-load.sh
kubectl apply -k overlays/local
kubectl -n customer360 rollout status deployment/dagster
```

For a local-only code change, rebuild and load the image before restarting the
legacy Deployment:

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

The VM script is currently a legacy deployment path. It runs one
`backend-system` container with `dagster dev`, uses host networking, and passes
an environment file assembled by the script. It does not deploy the Compose
webserver/daemon split and its object-storage configuration is currently
optional. Treat it as UAT/emergency-only until it is migrated.

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

The legacy container runs with host networking and exposes Dagster on port
`3000`. The Dagster UI should be exposed only through the intended private
network, SSH tunnel, load balancer, or authenticated proxy. Never publish the
Dagster UI directly to the public internet without an authenticated proxy and
TLS.

Emergency local build on the target VM:

```bash
BUILD_LOCAL=1 bash deployments/server/deploy-backend.sh uat
```

Use this only when GHCR is unavailable or while recovering the registry
pipeline. The normal path must build in CI and pull the resulting immutable
artifact. Before using this path for production, migrate the script to launch
the separate webserver and daemon services and to require PostgreSQL and S3.

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
3. Validate `workspace.yaml` and the resolved Compose configuration.
4. Run the identity-resolution, segmentation, analytics, and API Dagster-client
	test suites.
5. Execute placeholder jobs in-process and verify successful Dagster runs.
6. Start PostgreSQL, `dagster-db-init`, the webserver, and exactly one daemon
	in an isolated environment.
7. Verify `/server_info`, the API `/health` endpoint, and API-to-Dagster DNS.
8. Verify the generated instance config selects PostgreSQL and S3.
9. Run one controlled smoke job and verify its run and compute logs.
10. Generate an SBOM and scan the image before publishing it.

The focused local checks are:

```bash
cd backend-system
PYTHONPATH=. .venv/bin/python -m pytest -q identity_resolution/tests/test_dagster_defs.py
PYTHONPATH=. .venv/bin/python -m pytest -q --import-mode=importlib segmentation/tests/test_dagster_defs.py
PYTHONPATH=.:analytics .venv/bin/python -m pytest -q analytics/tests/test_tracking_log_aggregation.py
cd ..
docker compose config --quiet
```

The CI job should publish `customer360-dagster` only after these checks pass. A
release tag should publish the complete unified backend-system image.

## Operational checks

### Compose production checks

After deployment:

```bash
docker compose ps
docker compose logs --tail=200 dagster-db-init dagster dagster-daemon api
curl -fsS http://127.0.0.1:${DAGSTER_UI_PORT:-3000}/server_info
curl -fsS http://127.0.0.1:${C360_API_PORT:-8008}/health
docker compose exec api python -c \
	"import urllib.request; print(urllib.request.urlopen('http://dagster:3000/server_info', timeout=5).status)"
```

Check these failure signals immediately after rollout:

- `dagster-db-init` did not exit `0`.
- Either Dagster service reports SQLite storage.
- S3 readiness fails while `DAGSTER_REQUIRE_S3=true`.
- More than one `dagster-daemon` container is running.
- Any code location is in an error state in the Dagster UI.
- The API cannot resolve `dagster:3000`.
- Runs remain queued without daemon activity.

### Legacy Kubernetes and VM checks

These commands apply only to the legacy paths and do not prove the production
Compose topology:

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
	managed by exactly one Dagster daemon.
- PostgreSQL contains the Dagster instance state and S3 contains compute logs.
- No demo seed Job is enabled in production.

### Incident response

For a failed rollout, capture diagnostics before restarting or removing
containers:

```bash
docker compose ps -a
docker compose logs --no-color --tail=500 dagster-db-init dagster dagster-daemon api
docker inspect customer360-dagster customer360-dagster-daemon
```

Classify the failure before changing configuration:

- **Database failure:** restore PostgreSQL reachability or credentials; do not
	enable SQLite fallback.
- **Object storage failure:** restore endpoint, bucket, permission, or DNS; do
	not disable required S3 logging as an unreviewed workaround.
- **Code-location failure:** inspect the location logs and dependency versions;
	do not start a second daemon to compensate.
- **Queued runs with a healthy webserver:** inspect the singleton daemon logs,
	schedules, sensors, and run coordinator state.

After recovery, run one controlled job, verify its terminal status and logs, and
record the incident, image digest, database migration, and configuration change.

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
