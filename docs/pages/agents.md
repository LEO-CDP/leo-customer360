---
title: AI agents page
page: "/agent"
---

# AI agents page

This page is the registry of AI agents and models used across Customer 360: scoring, prediction, recommendation and prompt-backed agents. It is opened from the AI Agents tab in the top navigation. Each row is one agent; the Edit modal is where you view and change it.

## What you can do here
- **Read the summary cards**: Total agents (Registered), Active (with its share of the total), Training (In progress), Failed (Needs attention) and Updated today (Recent changes).
- **Switch tabs**: All agents, Active, Training, Failed and Inactive, each with a count.
- **Search**: "Search agents" by name, code, model or description.
- **Filter**: Type (All types plus the model types), Lifecycle (All status, Active, Inactive, Training, Deprecated, Failed), Runtime model (All models, Model configured, Model not configured), Prompt (All prompt states, Prompt-backed, No prompt) and Run mode (All run modes, Scheduled, On demand).
- **Reset**: clears the search and all filters.
- **Add AI Agent**: opens the create form.
- **Read the table**: Agent (display name and agent code), Agent Type, Model, Lifecycle, Prompt Version, Run Schedule, Input Features and Last Updated.
- **Edit an agent**: use the Edit action; rows do not open a detail page.

### The add/edit form
- **Fields**: Agent Code, Display Name, Runtime Model, Description, Model Type, Schedule Definition (cron), Agent Status, Input Features (comma-separated), Hyperparameters (JSON object), Prompt Key, Prompt Engine, System Instructions and Required Variables (comma-separated).
- **Model Type options**: Classification, Regression, Clustering, Ranking & Recommendation, Forecasting, Anomaly Detection, Uplift Modeling, Semantic Embedding, Graph ML, Optimization, Rules Engine, Generative LLM.
- **Agent Status options**: Active, Inactive, Training, Deprecated, Failed.
- **Schedule**: pick a preset (for example At midnight, Every morning at 09:00, Every weekday at 09:00) or Custom cron expression and type a five-field cron.
- **Save**: Save Agent for a new agent or Save Changes when editing; Cancel closes. Delete agent is available when editing. The Agent Code cannot be changed after creation.

## Common questions
- **Why is an agent Failed?** Its Lifecycle is Failed; open it with Edit and check its status, model and prompt configuration.
- **What is the difference between Active and Training?** They are lifecycle states; Training means the agent is still in progress.
- **How do I add an agent?** Add AI Agent, set the code, display name, model type and status, then save.
- **What does Prompt Version show?** The instruction version when a prompt key is set, otherwise "No prompt".
- **Where do I set the run schedule?** Schedule Definition (cron) in the form, using a preset or a custom five-field cron.
