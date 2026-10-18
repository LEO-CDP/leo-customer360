# Agentic campaign workflow

This document describes the campaign behavior that is implemented in the
repository today. The campaign is a workflow-owned execution record. It is
not a template-owned email campaign.

## Main campaign lifecycle

The only executable campaign path is:

```text
Select segment
  -> design workflow
  -> save campaign + workflow atomically
  -> review campaign and workflow
  -> approve campaign
  -> approve pinned workflow version
  -> activate campaign
  -> mark campaign Running
  -> run campaign_workflow_job
  -> load selected profiles
  -> process workflow logic per profile
  -> execute reachable action nodes
```

Activation requires all five conditions:

1. A selected `segment_id`.
2. A pinned `workflow_id`.
3. A pinned `workflow_version`.
4. `crm_campaign.approval_status = 'Approved'`.
5. `crm_flow_version.approval_status = 'Approved'`.

`crm_message_templates` is an independent AI-agent messaging/template
system. `crm_campaign` has no `template_id` and no direct foreign key to
`crm_message_templates`.

## Campaign metadata

The campaign editor persists these campaign fields:

| Field | Database field | Purpose |
| --- | --- | --- |
| Campaign code | `campaign_code` | Tenant-scoped internal reference |
| Name | `name` | Required campaign name |
| Operational status | `status` | Draft, Running, Paused, Completed, or Failed runtime state |
| Channel | `channel` | `email`, `sms`, `push`, or `audience_sync` |
| Platform | `platform` | Provider/platform matching the selected channel |
| Language | `lang` | Planning and content locale |
| Description | `description` | Campaign description |
| Keywords | `keywords` | Search/planning keywords |
| Start date | `start_date` | Planned start |
| End date | `end_date` | Planned end |
| Budget | `budget_amount` | Campaign budget |
| Currency | `currency` | Budget currency |
| Objective | `objective` | Awareness, Leads, Conversions, App Install, or Visit Store |
| Strategy summary | `strategy_summary` | Human description of the intended journey |
| Planning constraints | `planning_constraints` | Hard constraints for the planning agent |
| Owner | `user_id` | Authenticated campaign owner |
| Audience | `segment_id` | Selected Customer 360 segment |
| Workflow | `workflow_id` | Persisted workflow identity |
| Workflow version | `workflow_version` | Immutable version pinned to the campaign |
| Approval state | `approval_status` | Draft, InReview, Approved, or Rejected |
| Approval actor/time | `approved_by`, `approved_at` | Human approval evidence |
| Agent plan | `ai_plan` | Generated planning result and agent output |
| Metadata | `metadata` | Additional campaign metadata |

The editor implementation is:

- [`campaign-editor.html`](../../customer360-frontend/static/templates/campaign/campaign-editor.html)
- [`campaign-view.js`](../../customer360-frontend/static/js/campaign-view.js)

The editor does not expose a campaign template ID. The planning constraints
field is persisted as `crm_campaign.planning_constraints`; it is not silently
dropped from the form.

## Workflow design contract

The workflow studio is embedded by the campaign editor and stores schema
version 2 workflow JSON. The node catalog is defined in
[`workflow-core.js`](../../customer360-frontend/static/js/workflow/workflow-core.js).

### Input and logic nodes

| Node | Persisted type | Behavior |
| --- | --- | --- |
| Event trigger | `trigger` | Root node; selects the audience entry event |
| If / else | `condition` | Evaluates a profile field and chooses `yes` or `no` |
| Wait / Delay | `wait` | Advances virtual journey time |
| Wait for event | `wait_event` | Checks a linked profile event until timeout |
| Goal / Exit | `exit` | Ends the profile journey as goal or exit |

### Action nodes

| UI action | Persisted type | Node configuration | Runtime |
| --- | --- | --- | --- |
| Send email | `email` | `subject`, `body` | Sends the rendered subject/body to the profile's email |
| Send notification | `notification` | `provider`, `body` | Sends through `sms`, `zalo_oa`, `whatsapp`, or `mobile_native` |
| Send web push | `push` | `title`, `body` | Sends the rendered title/body to the profile's push tokens |
| Update profile / tag | `update_profile` | `action`, `tag` | Adds or removes a segmentation tag in `cdp_master_profiles` |
| Sync custom audience | `run_data_synch_job` | `platform`, `audience_name` | Submits one campaign-scoped audience export after the profile workflow pass |

The frontend studio, DAO validator, SQL node constraint, and API validation all
accept the same node types and configuration.

The notification provider is separate from the persisted node type. A
notification node with `provider: "sms"` targets the SMS connector; changing
the provider does not change the workflow node type.

Supported condition fields are real `cdp_master_profiles` fields:

- `engagement_score`
- `churn_probability`
- `lead_conversion_probability`
- `historical_clv`
- `latest_nps_score`
- `average_csat`
- `segmentation_tags`

Message templates support the runtime variable `{{first_name}}`. Campaign
workflow messages do not use `crm_message_templates`.

## Save and review APIs

The campaign editor saves campaign metadata and the workflow in one
transaction:

```text
POST  /api/v1/campaigns/with-workflow
PATCH /api/v1/campaigns/{campaign_id}/with-workflow
GET   /api/v1/campaigns/{campaign_id}/workflow
```

Implementation:

- [`workflow_api.py`](../../customer360-api/core/routers/workflow_api.py)
- [`campaign_workflow_repository.py`](../../customer360-api/core/repositories/campaign_workflow_repository.py)
- [`workflow.py`](../../customer360-dao/src/leo_customer360_dao/schemas/workflow.py)

The save transaction:

1. validates tenant ownership and the selected segment;
2. validates workflow structure and audience alignment;
3. saves an immutable workflow version;
4. pins `workflow_id` and `workflow_version` on `crm_campaign`;
5. commits campaign and workflow together.

Agent-authored workflow versions start `InReview`. A human tenant admin
reviews them through:

```text
POST /api/v1/workflows/{flow_id}/versions/{version}/review
```

User-created workflow versions may be approved immediately by the persistence
rules. A workflow version cannot be published or executed until its approval
state is `Approved`.

## Activation API

The activation endpoint is:

```text
POST /api/v1/admin/campaigns/{campaign_id}/activate
```

The API checks tenant ownership, human campaign approval, selected segment, and
pinned workflow presence before submitting Dagster. The backend implementation
is:

- [`campaign_activation_api.py`](../../customer360-api/core/routers/campaign_activation_api.py)
- [`campaign_activation_repository.py`](../../customer360-api/core/repositories/campaign_activation_repository.py)

The API returns the activation Dagster run ID. It does not load profiles or
send messages inside the HTTP request.

## Dagster and backend execution

Dagster definitions are in
[`dagster_defs.py`](../../customer360-backend/campaign_activation/dagster_defs.py).

### `campaign_activation_job`

`activate_campaign_op` calls
[`activate_campaign()`](../../customer360-backend/campaign_activation/campaign_activation/activation.py):

1. Load the campaign in the tenant RLS context.
2. Check campaign approval.
3. Check workflow version approval.
4. Check that the selected segment is active.
5. Set `crm_campaign.status = 'Running'`.
6. Submit `campaign_workflow_job`.

If Dagster submission fails, activation marks the campaign `Failed` rather
than leaving it permanently `Running`.

### `campaign_workflow_job`

`run_campaign_workflow_op` calls
[`run_campaign_workflow()`](../../customer360-backend/campaign_activation/campaign_activation/workflow_runner.py):

1. Require campaign status `Running`.
2. Load the pinned workflow with `crm_load_flow`.
3. Validate the workflow with the DAO `FlowEngine`.
4. Read active profiles whose `segmentation_tags` contain the selected
   segment's `segment_tag`, using keyset pagination.
5. Build a profile execution context for each profile.
6. Evaluate triggers, conditions, waits, and event waits.
7. Execute email, SMS, push, profile-tag, and audience-sync actions.
8. Record provider outcomes and errors.
9. Return execution counts for profiles, sends, tag updates, errors, and
   audience-sync runs.

Email, notification, and push connectors are resolved from active tenant
`crm_connector_config` rows. Missing explicit channel consent is skipped:

- `communication_preferences.email_opt_in == true`
- `communication_preferences.sms_opt_in == true`
- `communication_preferences.push_opt_in == true`

### Send notification providers

The Send notification node stores a `provider` configuration:

| Provider | Connector | Recipient data |
| --- | --- | --- |
| `sms` | `connector_type = 'SMS'` | `phone_number` |
| `zalo_oa` | `connector_type = 'CHAT'`, provider `ZALO` | `phone_number` |
| `whatsapp` | `connector_type = 'CHAT'`, provider `WHATSAPP` | `phone_number` |
| `mobile_native` | `connector_type = 'PUSH'` | `push_tokens` |

`zalo_oa` and `whatsapp` use the configured connector `webhook_url`. The
`mobile_native` provider uses the configured mobile notification API (for
example OneSignal) and the profile's native subscription tokens. Provider
credentials remain in connector `credentials` JSONB; the workflow never
stores provider secrets.

Existing notification workflows without `provider` remain valid and default to
the `sms` provider.

## Database model

The canonical schema is
[`database-schema.sql`](../../customer360-database/database-schema.sql).

Campaign workflow tables:

| Table | Role |
| --- | --- |
| `crm_campaign` | Campaign metadata, owner, segment, workflow pin, approval state, planning fields |
| `cdp_segments` | Selected audience definition and membership tag |
| `crm_flow` | Workflow identity and lifecycle |
| `crm_flow_version` | Immutable workflow JSON snapshot and governance state |
| `crm_flow_node` | Shredded workflow nodes for queries |
| `crm_flow_edge` | Parent/child workflow edges |
| `crm_campaign_workflow_action_runs` | Per-profile/node action idempotency and provider results |
| `crm_ad_sync_runs` | Campaign-scoped audience-sync audit state |
| `cdp_master_profiles` | Profiles loaded into workflow execution |
| `cdp_raw_profiles_stage` | Raw events used by wait-for-event execution |
| `cdp_profile_links` | Raw-to-master profile event linkage |
| `crm_connector_config` | Tenant provider configuration |
| `crm_suppression_list` | Global and campaign suppression rules |
| `crm_campaign_reviews` | Campaign human approval history |

For this development database, `database-schema.sql` is the source of truth.
Drop and recreate the database from the full schema; no incremental workflow
migrations are required.

## Idempotency and failure behavior

Per-profile action idempotency is keyed by:

```text
campaign_id + master_profile_id + node_id
```

The runner also uses a PostgreSQL advisory lock for that key so concurrent
Dagster retries cannot send the same action twice. Completed actions are
skipped. Failed actions are retried with an incremented attempt count.

Provider failures are isolated to the profile/action result. Other profiles
continue processing. A workflow-level failure prevents a successful
completion result and is visible in Dagster logs.

## Explicitly outside this campaign flow

- Direct campaign-to-`crm_message_templates` links.
- Campaign template IDs in the campaign editor.
- Segment-wide email or notification dispatch.
- Campaign activation through `email_engine_job` or `notification_engine_job`.
- Frontend-only approval or publish decisions.
- Cross-tenant profile access.
