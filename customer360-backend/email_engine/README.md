# `email_engine` — Email Campaign Delivery

The email engine sends one approved campaign to its approved segment. It
resolves recipients in keyset batches, skips missing-email and suppressed
profiles, renders templates, dispatches through the configured adapter, and
writes idempotent `cdp_campaign_dispatch_logs` records. Each recipient is
isolated with a database savepoint so one failure does not abort the batch.

## Dagster interface

- Job: `email_engine_job`
- Op: `send_campaign_op`
- Run config:

  ```yaml
  ops:
    send_campaign_op:
      config:
        campaign_id: "<uuid>"
        tenant_id: "<uuid>"
  ```

The implementation is in `email_engine/send.py`. The job is normally submitted
by `campaign_activation_job`, but can also be triggered directly by the API's
Dagster client.

## Package layout

| Path | Responsibility |
| --- | --- |
| `dagster_defs.py` | Dagster job and typed op configuration |
| `email_engine/send.py` | Campaign validation, recipient batching, rendering, dispatch, and ledger writes |
| `email_engine/adapters.py` | Provider adapter selection |
| `email_engine/connector_config.py` | Tenant-scoped provider configuration |
| `email_engine/rendering.py` | Template rendering and tracking URL injection |
| `email_engine/tracking.py` | Tracking-token generation |
| `tests/` | Delivery, adapter, rendering, and Dagster tests |

## Local development

```bash
cd customer360-backend/email_engine
./run_tests.sh
```

The service uses the database, DAO, dotenv, and Dagster dependencies declared
in `requirements.txt`.