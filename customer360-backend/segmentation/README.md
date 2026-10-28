# `segmentation` — Segment Recalculation

The segmentation service recomputes membership for every active
`cdp_segments` row and synchronizes `member_count` and
`cdp_master_profiles.segmentation_tags`. Runs can cover all tenants or be
scoped to one tenant and/or segment through run config.

## Dagster interface

- Job: `segmentation_job`
- Sensor: `segmentation_poll_sensor`, running by default
- Poll interval: `SEGMENTATION_POLL_INTERVAL_SECONDS` seconds (default `60`)
- Config fields: optional `tenant_id` and `segment_id`

The implementation is in `segmentation/recompute.py`. It uses PostgreSQL
temporary tables and Redis leases to bound memory use and prevent overlapping
recomputations.

## Package layout

| Path | Responsibility |
| --- | --- |
| `dagster_defs.py` | Job, op, sensor, and run configuration |
| `segmentation/recompute.py` | Active-segment membership and profile-tag recomputation |
| `segmentation/rls.py` | Tenant context for service connections |
| `tests/` | Dagster definition tests |

## Local development

```bash
cd customer360-backend/segmentation
./run_tests.sh
```