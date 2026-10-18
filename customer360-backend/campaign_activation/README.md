# Campaign workflow activation

`campaign_activation` is the only development execution path for campaigns.
Every campaign must have a selected segment and a pinned, approved persisted
workflow.

## Trigger

The API entry point is:

```text
POST /api/v1/admin/campaigns/{campaign_id}/activate
```

The route is tenant-scoped and tenant-admin protected. It requires:

- `crm_campaign.approval_status = 'Approved'`;
- `crm_campaign.segment_id`;
- `crm_campaign.workflow_id`;
- `crm_campaign.workflow_version`;
- the pinned `crm_flow_version.approval_status = 'Approved'`.

The activation API submits a Dagster run and returns its `run_id`; profile
execution is asynchronous.

## Activation sequence

`campaign_activation_job` runs `activate_campaign_op`, which:

1. loads the campaign in the caller's tenant;
2. validates campaign approval;
3. validates the selected segment is active;
4. validates the pinned workflow version is approved;
5. updates `crm_campaign.status` to `Running`;
6. submits `campaign_workflow_job`.

No template-based or channel-dispatch fallback is selected by
`activate_campaign`.

## Workflow execution

`campaign_workflow_job` runs `run_campaign_workflow_op`, implemented by
[`workflow_runner.py`](./campaign_activation/workflow_runner.py).

Direct Dagster configuration:

```yaml
ops:
  run_campaign_workflow_op:
    config:
      tenant_id: "<uuid>"
      campaign_id: "<uuid>"
```

The runner only loads campaigns whose status is `Running`. It then:

1. loads the exact workflow version with `crm_load_flow`;
2. validates the workflow through the DAO `FlowEngine`;
3. loads active profiles whose `segmentation_tags` contain the selected
   segment's `segment_tag`, using keyset pagination;
4. creates a `CdpMasterProfile` context for each profile;
5. processes the logical graph from the trigger node;
6. evaluates condition branches;
7. advances virtual time for wait nodes;
8. queries linked raw events for wait-for-event nodes;
9. activates every reachable action node;
10. records per-profile results and errors without stopping other profiles.

The workflow graph is the execution contract. The engine does not substitute
campaign-level templates or dispatch a whole segment outside the graph.

## Frontend-to-runtime contract

The editor saves workflow schema version 2 JSON from
[`workflow-core.js`](../../customer360-frontend/static/js/workflow/workflow-core.js).
The action node types map to runtime behavior as follows:

| Frontend node | Persisted type | Runtime action |
| --- | --- | --- |
| Send email | `email` | Render and send the node subject/body for that profile |
| Send notification | `notification` | Send through the node's SMS, Zalo OA, WhatsApp, or mobile-native provider |
| Send web push | `push` | Send the node title/body to that profile's push tokens |
| Update profile / tag | `update_profile` | Add/remove the configured tag in `cdp_master_profiles` |
| Sync custom audience | `run_data_synch_job` | Submit a campaign-scoped audience sync action |

The workflow studio build guide exposes the same action set. Its action
controls are in `workflow-studio.js` and `workflow-template.js`.

Campaign metadata in
[`campaign-editor.html`](../../customer360-frontend/static/templates/campaign/campaign-editor.html)
is aligned with the workflow:

- Channel: `email`, `notification`, `push`, or `audience_sync`;
- Platform: provider selection appropriate to that channel;
- Language: the configured planning/content locale.

## Action execution and idempotency

Per-profile email, SMS, push, and tag actions use
`crm_campaign_workflow_action_runs`, keyed by:

```text
campaign_id + master_profile_id + node_id
```

Completed action rows are skipped on Dagster retries. Failed rows are retried
with an incremented attempt count. Provider IDs and errors are recorded.

Audience-sync nodes are campaign-scoped rather than one provider request per
profile. The workflow runner submits one `data_synch_job` per unique configured
platform. That job loads the same selected segment, applies eligibility and
suppression rules, hashes identifiers, and records `crm_ad_sync_runs`.

## Providers and consent

Provider connectors are loaded from active tenant rows in
`crm_connector_config`:

- `EMAIL`: SMTP or test mock;
- `SMS`: configured webhook or test mock;
- `PUSH`: OneSignal or test mock.

Workflow delivery requires explicit consent:

- `email_opt_in == true`;
- `sms_opt_in == true`;
- `push_opt_in == true`.

Missing consent is skipped. Raw profile data is not written to logs.

## Persistence and development database

For this development database, initialize PostgreSQL from the complete
`customer360-database/database-schema.sql`. The workflow governance,
audience-sync, notification-node, action-ledger, campaign-template unlink, and
planning-constraints definitions are included directly in that schema; no
incremental workflow migrations are required.

## Package layout

| Path | Responsibility |
| --- | --- |
| [`dagster_defs.py`](./dagster_defs.py) | Activation and workflow Dagster jobs |
| [`activation.py`](./campaign_activation/activation.py) | Approval, segment validation, Running status, workflow submission |
| [`workflow_runner.py`](./campaign_activation/workflow_runner.py) | Profile loading, graph execution, providers, action ledger |
| [`triggers.py`](./campaign_activation/triggers.py) | Workflow Dagster submission |
| [`db.py`](./campaign_activation/db.py) | PostgreSQL connection |
| [`tests/`](./tests/) | Activation and workflow execution tests |

## Local development

```bash
cd customer360-backend
dagster dev -w workspace.yaml

cd campaign_activation
./run_tests.sh
```
