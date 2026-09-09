# Dagster Production Runbook: Queued Run Diagnosis

## 1. Purpose

This runbook defines the production checks for the Dagster orchestration layer in
`backend-system/`.

**Primary objective:** no production run remains in `QUEUED` without a documented
reason and an active recovery path.

The runbook covers:

- Dagster instance and shared-state validation;
- webserver, daemon, and code-location health;
- run coordinator and launcher diagnosis;
- manual and scheduled run verification;
- detection and alerting for stuck queued runs; and
- escalation boundaries between orchestration failures and job failures.

This document is an operational guide. It does not replace the deployment
runbook, capacity benchmarks, incident procedures, or application-specific
recovery instructions.

## 2. Production Invariants

The following conditions must be true in production:

1. The webserver and the singleton daemon use the same image, configuration,
   `DAGSTER_HOME`, and PostgreSQL-backed Dagster instance.
2. Exactly one `dagster-daemon` is active. Multiple daemons can create duplicate
   schedule or sensor runs because this deployment does not provide daemon leader
   election.
3. PostgreSQL is the durable store for Dagster run, event, and schedule state.
4. S3-compatible object storage is used for compute logs when
   `DAGSTER_REQUIRE_S3=true`.
5. The run coordinator is `QueuedRunCoordinator` with a positive concurrency
   limit.
6. The daemon is responsible for schedules, sensors, queued-run processing, and
   run monitoring.
7. Every required code location loads successfully before a job is approved for
   production execution.
8. A run follows the expected lifecycle:

   ```text
   QUEUED -> STARTING -> STARTED -> SUCCESS
   ```

A run may legitimately finish as `FAILURE`, `CANCELED`, or `CANCELING`. It must
not remain indefinitely in `QUEUED` or `STARTING`.

## 3. Supported Topology

The production Compose topology is:

```text
                    +----------------------+
                    |    dagster           |
                    |  webserver / GraphQL |
                    +----------+-----------+
                               |
                               | loads workspace
                               v
     +------------------------------------------------------+
     |  Nine Dagster code locations                         |
     |  identity_resolution  scoring       segmentation     |
     |  analytics             data_synch   email_engine     |
     |  notification_engine  campaign_activation           |
     |  personalization                                      |
     +------------------------------------------------------+
                               ^
                               |
                    +----------+-----------+
                    |  dagster-daemon      |
                    |  one singleton       |
                    +----------+-----------+
                               |
                 schedules, sensors, queue, monitoring
                               |
                    +----------v-----------+
                    | PostgreSQL + S3      |
                    | shared durable state |
                    +----------------------+
```

The nine code locations registered in `backend-system/workspace.yaml` are:

- `identity_resolution`
- `scoring`
- `segmentation`
- `analytics`
- `data_synch`
- `email_engine`
- `notification_engine`
- `campaign_activation`
- `personalization`

The current active jobs include `identity_resolution_job`, `analytics_job`, and
`segmentation_job`. Some other locations currently expose placeholder jobs; a
successful code-location load does not imply that every location contains an
active business pipeline.

## 4. Roles and Responsibilities

| Component | Responsibility | Failure symptom |
|---|---|---|
| `dagster` webserver | UI, GraphQL, code-location discovery, run submission | UI unavailable, job not visible, API submission fails |
| `dagster-daemon` | Schedule ticks, sensors, queued runs, run monitoring | Runs remain queued, ticks stop, sensors do not request runs |
| Code location / gRPC server | Loads definitions and executes the selected job process | Import error, job missing, run fails at startup |
| `QueuedRunCoordinator` | Holds queued runs and releases them within concurrency limits | Queue grows or never drains |
| Run launcher | Starts a worker process for a released run | Run stays `STARTING` or launch errors appear |
| PostgreSQL | Stores runs, events, schedules, sensors, and instance metadata | State mismatch, connection failures, startup refusal |
| S3-compatible storage | Stores durable compute logs | Missing logs or startup refusal when required |

## 5. Configuration Contract

### 5.1 Shared instance

The webserver and daemon must resolve the same `DAGSTER_HOME` and connect to the
same PostgreSQL-backed instance.

```bash
printf 'DAGSTER_HOME=%s\n' "${DAGSTER_HOME:-<unset>}"
printf 'DAGSTER_PG_DB=%s\n' "${DAGSTER_PG_DB:-<unset>}"
dagster instance info
```

In the production container, the instance configuration is rendered at startup
by `scripts/render_dagster_instance.py`. Do not hand-edit the generated
`$DAGSTER_HOME/dagster.yaml`; update the renderer or deployment environment
instead.

Verify the following:

- [ ] `DAGSTER_HOME` is set to the intended environment.
- [ ] Webserver and daemon use the same `DAGSTER_HOME` value.
- [ ] Both processes use the same image digest and environment contract.
- [ ] `run_storage` uses PostgreSQL.
- [ ] `event_log_storage` uses PostgreSQL.
- [ ] `schedule_storage` uses PostgreSQL.
- [ ] `run_coordinator` is `QueuedRunCoordinator`.
- [ ] The effective run concurrency is greater than zero.
- [ ] The effective run launcher is present and operational.

For the Compose deployment, run the command inside the running service so that
it observes the production environment rather than the operator's local shell:

```bash
docker compose exec dagster dagster instance info
docker compose exec dagster-daemon dagster instance info
```

The two outputs must describe the same instance. A difference such as
`run_coordinator: NoneType` in one container while the other expects
`QueuedRunCoordinator` is an **instance/configuration mismatch** and must be
resolved before investigating individual jobs.

### 5.2 Rendered queue and monitoring configuration

The production renderer writes the following effective configuration, with
values controlled by environment variables:

| Setting | Default | Purpose |
|---|---:|---|
| `DAGSTER_MAX_CONCURRENT_RUNS` | `2` | Maximum active runs released by the queue |
| `DAGSTER_RUN_START_TIMEOUT_SECONDS` | `300` | Time before a `STARTING` run is treated as orphaned |
| `run_monitoring.poll_interval_seconds` | `60` | Orphaned-run monitoring interval |
| `cancel_timeout_seconds` | `180` | Time allowed for cancellation |
| `max_resume_run_attempts` | `0` | Automatic resume attempts |

Verify the rendered file when troubleshooting:

```bash
docker compose exec dagster sh -lc \
  'grep -nE "run_coordinator|QueuedRunCoordinator|max_concurrent_runs|run_monitoring|start_timeout" \
   "$DAGSTER_HOME/dagster.yaml"'
```

Do not increase concurrency as the first response to a queue incident. Confirm
PostgreSQL capacity, S3 throughput, job memory, and downstream service limits
before changing `DAGSTER_MAX_CONCURRENT_RUNS`.

### 5.3 Durable dependencies

The production renderer fails closed when PostgreSQL is unavailable. S3 compute
logs are required when `DAGSTER_REQUIRE_S3=true`.

```bash
docker compose ps postgres dagster-db-init dagster dagster-daemon
docker compose logs --since=15m dagster-db-init
docker compose logs --since=15m dagster | tail -100
docker compose logs --since=15m dagster-daemon | tail -100
```

Verify:

- [ ] The dedicated Dagster PostgreSQL database exists and accepts connections.
- [ ] The `dagster-db-init` one-shot service completed successfully.
- [ ] The configured S3 bucket is reachable when S3 logs are required.
- [ ] The webserver and daemon did not silently fall back to SQLite or local-only
  compute logs.

## 6. Standard Diagnostic Procedure

Follow this order. Do not start with CPU or RAM unless the process or host
checks indicate a resource failure.

```text
DAGSTER_HOME and instance
        |
        v
PostgreSQL and S3 dependencies
        |
        v
webserver and singleton daemon
        |
        v
run coordinator and run launcher
        |
        v
code-location loading
        |
        v
manual job execution
        |
        v
schedule or sensor execution
        |
        v
resource and application diagnosis
```

Record the following before changing configuration:

- UTC timestamp and environment;
- affected `run_id`, `job_name`, and code location;
- current run status and queue age;
- webserver and daemon image digest;
- effective `DAGSTER_HOME` and instance configuration;
- daemon logs for the affected time window; and
- PostgreSQL and S3 health.

### 6.1 Check processes and container health

```bash
docker compose ps dagster dagster-daemon
docker compose top dagster
docker compose top dagster-daemon
```

For a host-level deployment:

```bash
ps -ef | grep '[d]agster'
ps -ef | grep '[d]agster._daemon'
```

Expected production state:

- one healthy webserver service;
- exactly one healthy daemon service;
- the expected code-location processes or gRPC servers;
- no repeated restart loop; and
- no recent exit code or health-check failure.

A process named `dagster._daemon` without a healthy service or current heartbeat
is not sufficient. Check logs and the effective instance configuration from the
same container.

### 6.2 Check daemon health and launch activity

```bash
docker compose logs --since=30m dagster-daemon | \
  grep -iE 'error|exception|traceback|launch|queued|starting|sensor|schedule|heartbeat' | \
  tail -200
```

For a host-mounted Dagster home:

```bash
grep -iE 'error|exception|traceback|launch|queued|starting|sensor|schedule|heartbeat' \
  "$DAGSTER_HOME"/logs/daemon/*.log | tail -200
```

Verify:

- [ ] Daemon heartbeats are current.
- [ ] Schedule evaluations produce ticks.
- [ ] Sensor evaluations produce ticks or explicit skip reasons.
- [ ] The daemon sees queued runs.
- [ ] Launch attempts are present for queued runs.
- [ ] No repeated PostgreSQL, gRPC, or launcher exception is present.

### 6.3 Check code locations

List jobs from the workspace used by the deployment:

```bash
cd backend-system
dagster job list -w workspace.yaml
```

Expected jobs and locations must include the relevant production job, for
example:

```text
analytics_job       analytics
segmentation_job    segmentation
identity_resolution_job  identity_resolution
```

If a job is missing:

1. Check the corresponding `dagster_defs.py` import error.
2. Check the location's `requirements.txt` and installed dependencies.
3. Check the workspace path and `location_name`.
4. Check the gRPC or code-location startup log.
5. Restart the webserver and code-location process with the same image only
   after collecting the error.

A missing job is a code-location or workspace problem, not a queue problem.

### 6.4 Check the run coordinator and launcher

```bash
dagster instance info
```

The effective configuration must show a queue coordinator and a functioning
launcher. Confirm that:

- `QueuedRunCoordinator` is loaded;
- `max_concurrent_runs` is a positive integer;
- no deployment variable sets the limit to `0`;
- the daemon is running against the same instance; and
- launcher errors are absent from daemon logs.

If runs are `QUEUED` while `max_concurrent_runs` is already at capacity, this is
normal back-pressure only when active runs are progressing. It becomes an
incident when the active runs are stale, no launch attempts occur, or queue age
exceeds the service-level threshold.

## 7. Manual Run Verification

Always validate a manual run before diagnosing a schedule. This separates job
execution from scheduler and sensor behavior.

### 7.1 Analytics job

From `backend-system/`, using an environment containing the analytics
requirements:

```bash
dagster job list -w workspace.yaml
dagster job execute \
  -f analytics/dagster_defs.py \
  -j analytics_job
```

If the production service uses the loaded workspace rather than a direct Python
file, execute the equivalent run through the UI or the deployment's run-submit
API. Capture the resulting `run_id` and verify its lifecycle in the UI, GraphQL
API, or database.

### 7.2 Segmentation job

`segmentation_job` recomputes active segment membership. It may process all
tenants when no run config is supplied, so use a scoped run configuration for a
production investigation whenever the deployment supports it.

```bash
dagster job execute \
  -f segmentation/dagster_defs.py \
  -j segmentation_job
```

Do not use a full multi-tenant recompute as a harmless smoke test on a large
production database. Use a controlled tenant or segment scope through the
application's supported Dagster submission path.

### 7.3 Interpret the result

| Observed lifecycle | Interpretation |
|---|---|
| Run is not created | Submission, permissions, webserver, or code-location issue |
| Run remains `QUEUED` | Daemon, coordinator, launcher, instance, or concurrency issue |
| Run reaches `STARTING` and stalls | Launcher, worker startup, or code-location process issue |
| Run reaches `STARTED` and fails | Job code, dependency, database, S3, or application issue |
| Manual run succeeds | Job and launcher path work; continue with schedule/sensor diagnosis |
| Manual run fails before op execution | Code location, import, launcher, or environment issue |

The minimum successful smoke-test lifecycle is:

```text
QUEUED -> STARTING -> STARTED -> SUCCESS
```

## 8. Schedule and Sensor Verification

Run this section only after a manual run succeeds.

### 8.1 Analytics schedule

`analytics_hourly_schedule` is configured with:

- cron: `*/3 * * * *`;
- timezone: `GMT`; and
- default status: `RUNNING`.

Verify:

- [ ] The schedule is enabled in the active instance.
- [ ] A schedule tick is created at the expected UTC time.
- [ ] The tick creates a run.
- [ ] The daemon submits the run to the queue.
- [ ] The run leaves `QUEUED` and completes.
- [ ] The next tick is not blocked by the preceding run.

Expected sequence:

```text
10:00 UTC -> run -> SUCCESS
10:03 UTC -> run -> SUCCESS
10:06 UTC -> run -> SUCCESS
```

A schedule that creates no run is a schedule/daemon/instance problem. A schedule
that creates runs which never leave `QUEUED` is a queue/launcher/daemon problem.
A run that starts and fails is an application or dependency problem.

### 8.2 Segmentation sensor

The segmentation sensor polls for changed `cdp_master_profiles` rows. Its
interval is controlled by `SEGMENTATION_POLL_INTERVAL_SECONDS` and defaults to
10 seconds.

Verify:

- [ ] The sensor is enabled.
- [ ] Sensor ticks show a current cursor.
- [ ] A changed profile produces a `RunRequest`.
- [ ] An unchanged database produces an explicit skip reason.
- [ ] A requested `segmentation_job` run leaves `QUEUED`.

A sensor tick with `DB check failed` is a database or application connectivity
incident. A successful `RunRequest` followed by a growing queue is an
orchestration incident.

## 9. Stuck Queue Detection and Alerting

### 9.1 Queue age query

Run against the dedicated Dagster PostgreSQL database:

```sql
SELECT
    run_id,
    job_name,
    status,
    create_timestamp,
    NOW() - create_timestamp AS queued_for
FROM runs
WHERE status = 'QUEUED'
ORDER BY create_timestamp;
```

For an alert threshold of five minutes:

```sql
SELECT
    run_id,
    job_name,
    create_timestamp,
    NOW() - create_timestamp AS queued_for
FROM runs
WHERE status = 'QUEUED'
  AND create_timestamp < NOW() - INTERVAL '5 minutes'
ORDER BY create_timestamp;
```

**Expected result for the alert query: zero rows.**

The alert should include the oldest `run_id`, `job_name`, queue age, active run
count, configured concurrency, and the last daemon heartbeat.

### 9.2 Starting-run timeout

The renderer configures run monitoring with a five-minute `STARTING` timeout.
Investigate any run that remains `STARTING` beyond that interval:

```sql
SELECT
    run_id,
    job_name,
    status,
    create_timestamp,
    update_timestamp,
    NOW() - update_timestamp AS unchanged_for
FROM runs
WHERE status = 'STARTING'
ORDER BY update_timestamp;
```

A `STARTING` timeout usually points to a launcher or worker-start problem rather
than a queue coordinator problem.

### 9.3 Recommended alert policy

| Condition | Severity | Initial action |
|---|---|---|
| Any `QUEUED` run older than 5 minutes | High | Inspect daemon, coordinator, launcher, and instance mismatch |
| Any `STARTING` run older than 5 minutes | High | Inspect worker launch and code-location startup |
| Daemon heartbeat absent for 2 intervals | Critical | Verify singleton daemon process and PostgreSQL access |
| Two or more daemon processes | Critical | Stop duplicate daemon after preserving incident evidence |
| Schedule tick absent for one expected interval | High | Check schedule status, timezone, daemon, and instance |
| PostgreSQL unavailable | Critical | Restore Dagster metadata dependency before relaunching |

## 10. Resource and Application Checks

Run resource checks after the orchestration path is verified, or when logs show
an explicit resource failure.

```bash
ps -eo pid,%cpu,%mem,rss,cmd --sort=-%mem | grep '[d]agster'
free -h
df -h /
dmesg -T | grep -iE 'out of memory|oom|killed process'
```

Check for:

- [ ] OOM kill or repeated container restart.
- [ ] A Dagster process at approximately 100% CPU for an unexplained duration.
- [ ] Exhausted local disk or Docker storage.
- [ ] PostgreSQL connection exhaustion or slow queries.
- [ ] S3 timeouts or compute-log upload failures.
- [ ] Application-level failures in the job op logs.

Do not increase queue concurrency to compensate for slow SQL, high memory use,
S3 throttling, or an application failure. Correct the limiting dependency or move
large runs to isolated workers after a capacity test.

## 11. Diagnosis Decision Matrix

| Symptom | Most likely boundary | Next checks |
|---|---|---|
| Schedule tick is absent | Daemon, schedule, or instance | Daemon heartbeat, schedule status, `DAGSTER_HOME`, timezone |
| Tick exists but no run is created | Schedule evaluation or submission | Tick error, webserver logs, permissions, code location |
| Run is created and remains `QUEUED` | Queue/daemon/launcher | Queue config, daemon launch logs, concurrency, queue-age SQL |
| `run_coordinator` is `NoneType` unexpectedly | Instance/config mismatch | Compare `DAGSTER_HOME`, generated YAML, image and env |
| Run reaches `STARTING` and stalls | Launcher/worker startup | Launcher logs, gRPC health, process creation, start timeout |
| Job is absent from `dagster job list` | Workspace/code location | `workspace.yaml`, imports, requirements, location logs |
| Run reaches `STARTED` then fails | Job/application/dependency | Op logs, PostgreSQL, S3, Redis, input data |
| Duplicate scheduled runs appear | More than one daemon | Process list, Compose replica count, deployment topology |
| Queue drains only after restart | Stale daemon, launcher, or dependency | Heartbeat, connection pool, orphan monitoring, restart history |

## 12. Recovery Boundaries

### Safe first actions

1. Capture run IDs, logs, instance information, and queue age.
2. Confirm there is exactly one daemon.
3. Confirm PostgreSQL and S3 dependencies are healthy.
4. Compare webserver and daemon configuration.
5. Inspect the rendered instance configuration.
6. Retry one controlled manual run after the cause is understood.

### Actions requiring an incident decision

- increasing `DAGSTER_MAX_CONCURRENT_RUNS`;
- canceling or deleting queued runs;
- restarting the daemon while schedule runs may be in flight;
- changing `DAGSTER_REQUIRE_S3` to bypass a storage outage;
- switching storage to SQLite or local compute logs; and
- deploying a new image while the queue is already unhealthy.

Never delete run history to hide a queue symptom. Preserve the metadata needed
for root-cause analysis.

## 13. Production Sign-off Checklist

```text
[ ] DAGSTER_HOME is correct and shared by webserver and daemon
[ ] Webserver and daemon use the same image and environment contract
[ ] Exactly one dagster-daemon is running
[ ] PostgreSQL run/event/schedule storage is reachable
[ ] S3 compute-log storage is reachable when required
[ ] All nine workspace code locations load successfully
[ ] QueuedRunCoordinator is active with concurrency > 0
[ ] Run launcher is operational
[ ] Daemon heartbeat and launch logs are current
[ ] Manual analytics_job: QUEUED -> STARTING -> STARTED -> SUCCESS
[ ] Manual segmentation_job tested with a controlled scope
[ ] Analytics schedule creates and launches the next run
[ ] Segmentation sensor produces expected RunRequest or SkipReason
[ ] QUEUED older than 5 minutes returns zero rows
[ ] STARTING older than 5 minutes returns zero unexplained rows
[ ] No OOM, disk, PostgreSQL, or S3 incident is open
```

## 14. Incident Summary Template

Use this format when escalating a Dagster orchestration incident:

```text
Environment:
Detected at UTC:
Affected job(s):
Affected code location(s):
Oldest run_id:
Oldest queue age:
Current QUEUED count:
Current STARTING count:
DAGSTER_HOME:
Dagster image digest:
Run coordinator:
Run launcher:
Configured max_concurrent_runs:
Last daemon heartbeat:
PostgreSQL status:
S3 status:
Manual run result:
Schedule/sensor result:
Relevant log excerpt:
Actions already taken:
Next owner:
```
