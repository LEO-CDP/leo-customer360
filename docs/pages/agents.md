---
title: AI agents page
page: "/agent"
---

# AI agents page

> **Vietnamese version:** [`agents-vi.md`](agents-vi.md)

This page is the registry of AI agents, machine-learning models, and rule engines (`cdp_ai_agents`) used across Customer 360 for scoring, prediction, recommendation, and prompt-backed campaign/segment generation. Open it from the **AI Agents** tab in the top navigation. Each row is one registered agent; the **Add/Edit AI Agent** modal is where you inspect and update its configuration, schedule, and versioned system prompt.

## What you can do here
- **Read the summary cards**: Total agents (Registered), Active (with its share of the total), Training (In progress), Failed (Needs attention), and Updated today (Recent changes).
- **Switch tabs**: All agents, Active, Training, Failed, and Inactive, each displaying a live count.
- **Search**: "Search agents" filters by display name, agent code, runtime model, or description.
- **Filter**: Type (All types plus the 10 canonical model types), Lifecycle (All status, Active, Inactive, Training, Deprecated, Failed), Runtime model (All models, Model configured, Model not configured), Prompt (All prompt states, Prompt-backed, No prompt), and Run mode (All run modes, Scheduled, On demand).
- **Reset**: clears the search box and all filter dropdowns.
- **Add AI Agent**: opens the create modal.
- **Read the table**: Agent (display name and `agent_code`), Agent Type, Model, Lifecycle, Prompt Version, Run Schedule, Input Features, and Last Updated.
- **Edit an agent**: use the **Edit** action on a row; rows do not open a separate detail page.

### The add/edit form
- **Fields**: Agent Code (`agent_code`), Display Name, Runtime Model (`model_name`), Description, Model Type (`model_type`), Schedule Definition (cron), Agent Status, Input Features (comma-separated feature codes from `cdp_ai_feature_catalog`), Hyperparameters (JSON object), Prompt Key, Prompt Engine, System Instructions, and Required Variables (comma-separated).
- **Model Type options**: Classification, Regression, Clustering, Ranking & Recommendation, Forecasting, Anomaly Detection, Uplift Modeling, Optimization, Rules Engine, Generative LLM.
- **Agent Status options**: Active (`ACTIVE`), Inactive (`INACTIVE`), Training (`TRAINING`), Deprecated (`DEPRECATED`), Failed (`FAILED`).
- **Schedule**: pick a preset (for example At midnight, Every morning at 09:00, Every weekday at 09:00) or **Custom cron expression** and enter a five-field cron string.
- **Prompt versioning**: updating **System Instructions** automatically increments `instruction_version` and appends an immutable entry to `prompt_versions` JSONB history.
- **Save**: **Save Agent** for a new agent or **Save Changes** when editing; **Cancel** closes the modal. **Delete agent** is available when editing. The **Agent Code** cannot be changed after creation.

## Common questions
- **Why is an agent Failed?** Its lifecycle status is `FAILED`; open it with **Edit** and review its status, runtime model, hyperparameters, and prompt configuration.
- **What is the difference between Active and Training?** They are lifecycle states; `ACTIVE` agents are ready for workflow/scheduled execution, while `TRAINING` indicates the model or agent is still being prepared or calibrated.
- **How do I add an agent?** Select **Add AI Agent**, set the agent code, display name, model type, and status, then select **Save Agent**.
- **What does Prompt Version show?** It displays `v<instruction_version>` when a prompt key or system instruction is configured, otherwise "No prompt".
- **Where do I set the run schedule?** Use **Schedule Definition (cron)** in the form, choosing either a preset or a custom five-field cron expression.
