# Campaign Marketing Workflow

## Purpose

The customer journey workflow is a feature inside the campaign marketing flow. It is embedded in the campaign editor's governed campaign plan and uses the campaign's selected audience. Campaign and workflow data are saved together as one atomic request, with the campaign linked to an immutable workflow version. The editor's simulation remains a local preview, not a message-delivery engine.

This document describes the current frontend behavior and separates it from follow-up work. The primary implementation sources are [campaign-editor.html](../../customer360-frontend/static/templates/campaign/campaign-editor.html) and [workflow-studio.js](../../customer360-frontend/static/js/workflow/workflow-studio.js), with campaign orchestration in [campaign-view.js](../../customer360-frontend/static/js/campaign-view.js).

## Campaign flow

1. **Open the campaign editor.** The `/campaigns/new/edit` route mounts the editor for a new campaign. `/campaigns/{id}/edit` first loads an existing campaign and fills its metadata. The editor includes campaign identity, channel/platform, schedule, budget, audience, customer journey workflow, objective, strategy summary, and planning constraints.
2. **Select an eligible audience.** The segment picker lists saved segments and only offers segments for which `is_active === true` and `status_code === 1`. The UI searches the loaded list by segment name, tag, or description. The selected segment is stored in the campaign's `segment_id` field and drives the workflow studio. A segment that is inactive or lacks a computed membership snapshot is shown with a warning and is not used to load profiles.
3. **Design and simulate a journey.** Once a ready segment is selected, the campaign view mounts the workflow studio in the editor and loads up to 32 matched profiles. Marketers can start from a recipe, arrange and configure journey nodes, inspect validation checks and workflow JSON, and run a deterministic local simulation against those profiles.
4. **Complete campaign planning fields.** The editor presents Objective as a single-select (`Awareness`, `Leads`, `Conversions`, `App Install`, or `Visit Store`), Strategy summary as a narrative describing the intended journey, and Planning constraints as hard limits for marketing planning. An existing campaign objective outside the configured choices is retained as an additional option when editing. The editor also displays AI-plan/provenance as read-only information where campaign details provide it.
5. **Save the campaign and workflow together.** The editor validates the campaign name, requires a ready audience, checks that the end date is not before the start date, and waits for the workflow studio to be ready. It submits the campaign fields and current workflow JSON in one request: new campaigns use `POST /campaigns/with-workflow`; existing campaigns use `PATCH /campaigns/{id}/with-workflow`. The API validates the tenant, audience/workflow segment match, and workflow structure, then commits the campaign changes and workflow version in one database transaction. A failure rolls back both. On success the UI navigates to campaign details.
6. **Review and manage the campaign.** Campaign details display operational status, approval status, audience, the saved customer journey directly below the audience in the overview, strategy, AI plan, agent provenance, history, and reporting. The workflow studio is mounted in read-only mode, which hides its recipe selector, clear/run controls, and side panel; a pointer-blocking overlay prevents canvas interaction. The saved version is loaded using `GET /campaigns/{id}/workflow`. The frontend exposes approve/reject actions to reviewer roles when approval status is `InReview`. Operational status (Draft, Active, Paused, Completed) is distinct from approval status.
7. **Measure and experiment.** Campaign reporting loads campaign metrics from the report API, with an analytics fallback when the single-campaign report endpoint returns 404. The details view also supports campaign experiments, which compare audience-segment variants. These are campaign lifecycle features separate from the journey canvas simulator.

## Frontend components and ownership

| Component | Responsibility |
| --- | --- |
| [campaign-editor.html](../../customer360-frontend/static/templates/campaign/campaign-editor.html) | Campaign editor form, saved-segment picker, embedded customer journey area, planning fields, and save action. |
| [campaign-view.js](../../customer360-frontend/static/js/campaign-view.js) | Campaign route and editor lifecycle, audience selection/loading, API requests, campaign save, approval controls, reporting, and experiments. |
| [workflow_api.py](../../customer360-api/core/routers/workflow_api.py) and [campaign_workflow_repository.py](../../customer360-api/core/repositories/campaign_workflow_repository.py) | Tenant-scoped campaign/workflow save and load endpoints, coordinating campaign governance and workflow persistence within one transaction. |
| [workflow-studio.js](../../customer360-frontend/static/js/workflow/workflow-studio.js) | Journey canvas, node editing, validation UI, JSON panel, profile/check/report panels, simulation action, and host integration. |
| [workflow-core.js](../../customer360-frontend/static/js/workflow/workflow-core.js) | Workflow node registry, schema validation, safe edit operations, simulator, sample profiles, and agent manifest. |
| [workflow-template.js](../../customer360-frontend/static/js/workflow/workflow-template.js) | Studio markup and industry journey recipes. |
| [workflow-studio-frame.html](../../customer360-frontend/static/js/workflow/workflow-studio-frame.html) | Loads the studio's browser dependencies in the same-origin iframe. |
| [flowy.js](../../customer360-frontend/static/js/workflow/flowy.js) and [flowy.css](../../customer360-frontend/static/css/flowy.css) | Canvas block layout/drag behavior and its styles. |
| [repository.py](../../customer360-dao/src/leo_customer360_dao/workflow/repository.py), [models.py](../../customer360-dao/src/leo_customer360_dao/workflow/models.py), and [010_campaign_workflow_link.sql](../../customer360-database/migrations/010_campaign_workflow_link.sql) | Versioned workflow storage contract, workflow schema, and tenant-safe campaign-to-version foreign key. |

The studio is hosted in an iframe so the campaign editor can embed the canvas without sharing its page styles or Flowy instance. The frame loads jQuery, Handlebars, Flowy, the workflow core, templates, and the studio script. The host waits for the studio's `onReady` callback before using the frame API.

## Audience-to-simulation data flow

The campaign view obtains segment candidates from `GET /segments/` (up to 100), filters to active computed segments, and fetches selected segment details from `GET /segments/{segment_id}`. It mounts the editor with that segment's ID and name. When the audience changes, the view calls `setSegment` on the mounted studio; clearing or invalidating the audience prevents the studio from loading profiles.

The studio's `loadProfiles` callback calls `GET /segments/{segment_id}/matched-profiles` with `skip=0` and `limit=32`. The campaign view maps returned customer profiles into the simulator's profile shape:

- Display name from the real-name helper or persona name, with a profile-ID fallback.
- Engagement score, cart value from profile attributes, and days since last activity.
- Email/SMS/push eligibility from email address, phone number, and push tokens.
- Segmentation tags copied from the profile.

The `id` passed to the simulator is a sequential index within the loaded result page; it is not the customer master-profile ID. This mapped data is for the browser simulation and profile preview.

## Journey model and behavior

The canvas represents a version-2 workflow JSON tree. Each node has an ID, type, configuration, and ordered children. The root is an Event trigger associated with the campaign's selected segment. Available node types are:

| Node | Purpose |
| --- | --- |
| Event trigger | Select an entry event and bind the journey to the selected audience. |
| If / else | Branch on profile score, cart, recency, push eligibility, or a tag. |
| Wait / Delay | Advance simulated time by minutes, hours, or days. |
| Wait for event | Branch on a simulated purchase, email open, or email click versus timeout. |
| Send email / notification / web push | Model a channel action with subject/title/body text. |
| Update profile / tag | Add or remove a tag for later conditions in the same simulated path. |
| Goal / Exit | End a path and label it as goal reached or exit. |

Condition children use `yes` and `no` branches; wait-for-event children use `happened` and `timeout`. Workflow validation checks the root, IDs, node types, required configuration, child limits, branches, and message actions. Missing a message is an error and blocks simulation; a missing goal, open branch, or back-to-back messages is reported as a warning or informational issue.

Message templates support `{{first_name}}` and `{{cart_value}}`. Simulation runs locally with fixed illustrative probabilities and deterministic pseudo-random outcomes per profile/node. The report shows simulated reach, delivery/engagement, conversion, revenue, and elapsed journey time; these are not campaign performance metrics.

Recipes and the JSON/agent manifest are editing aids. The manifest exposes node rules, recipes, and methods such as `getWorkflow`, `setWorkflow`, `applyOps`, `validate`, `simulate`, `run`, and `clear`; the frontend does not make an AI-agent request merely by exposing this interface.

## Persistence and execution boundary

Campaign workflows use the DAO's versioned `customer360.crm_flow*` persistence. `crm_campaign.workflow_id` and `workflow_version` reference the exact immutable workflow version belonging to the same tenant. The API calls the DAO `WorkflowRepository` inside the campaign request's transaction, and only commits after both the campaign and workflow writes succeed.

When opening an existing campaign, the frontend loads `GET /campaigns/{id}/workflow` and initializes the studio from that saved version. If no workflow has yet been associated with a legacy campaign, the response contains a null workflow and the studio starts with its default trigger. Each Save campaign request persists a new workflow version only when the submitted definition differs from the currently linked version; campaign edits and the campaign's version pointer are committed atomically. Workflow changes participate in the campaign draft audit/re-review behavior.

The studio's **Run campaign** button still only invokes the local simulator. It does not publish or execute the persisted version and does not send email, SMS, or push messages. Campaign creation/edit APIs and approval/rejection remain separate governed lifecycle concerns. Server-side APIs are authoritative for tenant scope, audience validity, workflow schema, campaign draft state, and approval.

`budget_time_constraints` is present in the form but is not included in the campaign/workflow save request or sent to an AI planning endpoint. It should not be described as an active planning integration. `strategy_summary` is persisted with campaign metadata and describes the intended strategy; it is separate from the canvas workflow JSON.

## Follow-up work

The following are not implemented by the workflow save integration:

1. Connect Planning constraints to an AI planning endpoint, including request/response/error handling and persistence of the approved plan/provenance.
2. Define governed publication, scheduling, and production execution semantics for a persisted workflow if message delivery is in scope. The workflow persistence endpoint does not publish or run flows.

Do not treat browser validation, profile previews, or simulated results as authorization, an approval decision, or delivery guarantees. Persisted workflow versions are durable campaign configuration, but are not evidence of execution.
