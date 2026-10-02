# `notification_engine` — Zalo ZNS Delivery

The notification engine currently implements Zalo ZNS campaign delivery and
Zalo OA token maintenance. It validates approved `zalo_zns` campaigns,
resolves eligible phone recipients, binds typed template parameters, dispatches
through the selected adapter, and writes idempotent dispatch-log records.

It also projects Zalo opt-out events from the S3 event lake onto
`cdp_master_profiles.communication_preferences`.

## Dagster interface

- `zalo_token_refresh_job`
- `notification_engine_job`
- `zalo_optout_projection_job`
- Schedules: `zalo_token_refresh_schedule` and
  `zalo_optout_projection_schedule`, each every five minutes

The implementation is in `notification_engine/`. Campaign sends use
`send_zalo_campaign`; token refresh uses `refresh_due_tokens`; opt-out
projection uses `project_optout_events`.

## Package layout

| Path | Responsibility |
| --- | --- |
| `dagster_defs.py` | Jobs, ops, schedules, and typed campaign configuration |
| `notification_engine/send.py` | ZNS campaign validation, recipient batching, dispatch, and ledger writes |
| `notification_engine/adapters.py` | ZNS provider adapter selection |
| `notification_engine/provider_config.py` | Tenant-scoped Zalo connector and OA token loading |
| `notification_engine/optout_projection.py` | S3-first opt-out event projection |
| `notification_engine/token_refresh.py` | Near-expiry OA token refresh |
| `tests/` | Pure notification-engine tests |

## Local development

```bash
cd customer360-backend/notification_engine
./run_tests.sh
```