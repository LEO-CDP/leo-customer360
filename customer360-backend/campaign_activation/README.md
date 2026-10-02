# `campaign_activation` — Campaign Activation

This code location validates and starts approved campaigns. It verifies the
campaign, template, segment, tenant, and approval state; snapshots the target
segment size; marks the campaign as `Running`; and submits the appropriate
email or notification engine job.

## Dagster interface

- Job: `campaign_activation_job`
- Op: `activate_campaign_op`
- Run config:

  ```yaml
  ops:
    activate_campaign_op:
      config:
        campaign_id: "<uuid>"
        tenant_id: "<uuid>"
  ```

The implementation is in `campaign_activation/activation.py`. Activation is
idempotent: reactivating a running campaign resubmits the downstream
idempotent dispatch flow.

## Package layout

| Path | Responsibility |
| --- | --- |
| `dagster_defs.py` | Dagster job and typed op configuration |
| `campaign_activation/activation.py` | Approval checks, segment snapshot, status update, and downstream trigger |
| `campaign_activation/triggers.py` | Downstream email/Zalo Dagster submission |
| `campaign_activation/db.py` | PostgreSQL connection settings |
| `tests/` | Activation and Dagster definition tests |

## Local development

```bash
cd customer360-backend
dagster dev -w workspace.yaml
```

Run the service tests from its directory:

```bash
cd customer360-backend/campaign_activation
./run_tests.sh
```