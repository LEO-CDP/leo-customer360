# Customer 360 Documentation Hub

Central index for architecture specifications, data source contracts, identity resolution designs, marketing activation workflows, UI guides, and operational runbooks across the **LEO Customer 360** repository.

---

## Quick Navigation by Component

| Component | Service Documentation | Primary Scope |
|---|---|---|
| [`customer360-database/`](../customer360-database/README.md) | Database Schema & Migrations | PostgreSQL 16 (`customer360` schema), RLS policies, triggers, and [Full Schema Diagram](../customer360-database/full-schema-diagram.md) |
| [`customer360-dao/`](../customer360-dao/README.md) | Shared Data Access Package | SQLAlchemy 2.0 models, Pydantic schemas, tenant-scoped repositories, and `FlowEngine` |
| [`customer360-event-api/`](../customer360-event-api/README.md) | Behavioral Event Ingestion API | FastAPI + Redis Streams + S3/MinIO Bronze `.jsonl.gz` ingestion (`:8010`) and [UAT Performance Report](../customer360-event-api/tests/reports/PERF_REPORT.md) |
| [`customer360-backend/`](../customer360-backend/README.md) | Dagster Orchestration Workspace | 8 Dagster code locations: [Identity Resolution](../customer360-backend/identity_resolution/README.md), [Segmentation](../customer360-backend/segmentation/README.md), [Analytics](../customer360-backend/analytics/README.md), [AI Agents Runners](../customer360-backend/ai_agents_runners/README.md), [Data Synch](../customer360-backend/data_synch/README.md), [Campaign Activation](../customer360-backend/campaign_activation/README.md), [Email Engine](../customer360-backend/email_engine/README.md), [Notification Engine](../customer360-backend/notification_engine/README.md) |
| [`customer360-api/`](../customer360-api/README.md) | Core REST & MCP API | Authenticated Customer 360 REST endpoints and `/mcp` tool surface (`:8008`) |
| [`customer360-agent/`](../customer360-agent/README.md) | AI Campaign Planning Service | LiteLLM-backed campaign & Zalo ZNS draft planner (`:8009`) |
| [`customer360-promotions/`](../customer360-promotions/README.md) | Promotions & Ad Serving API | Placements, creatives, sponsored content, and recommendations (`:9009`, `leo_ads` schema) |
| [`customer360-frontend/`](../customer360-frontend/README.md) | Admin & Operator Web UI | Browser shell and operator dashboards (`:8890`) |
| [`deployments/`](../deployments/README.md) & [`k8s/`](../k8s/README.md) | Infrastructure & Kubernetes | Terraform, VM deployment scripts, reverse proxy routing, and VKS/Kubernetes manifests |

---

## Architecture & Roadmap

Platform architecture, multi-tenant data model, end-to-end data journeys, and engineering roadmap.

- [Technical Documentation](architecture/TECHNICAL-DOCUMENTATION.md) — System architecture, data model, security boundaries, and service interactions
- [Product & Engineering Roadmap](architecture/ROADMAP.md) — Delivered milestones and upcoming capabilities
- [LEO Data Journey Maps](architecture/leo-data-journey-maps.md) — End-to-end customer lifecycle and touchpoint flows
- [Architecture Diagrams](architecture/images/) — Visual system, data lake, and composable CDP diagrams

## Data Sources & Event Tracking

Specifications for the five supported data ingestion modalities (`sys_data_source.source_type` 1–5) and Web SDK testing tools.

- [Data Source Types Overview](data-sources/data-source-types.md) — Architectural taxonomy of all 5 ingestion types
- [Type 1: Web JavaScript SDK Tracking](data-sources/1-web-sdk-tracking.md) — `leo.proxy.js`, cross-origin iframe, `leo.observer.js`, and consent controls
- [Type 2: Data Connector API (Server-side Pull)](data-sources/2-data-connector.md) — Scheduled connector sync specifications
- [Type 3: Data Webhook API (Server-side Push)](data-sources/3-web-hook-api.md) — Real-time HTTP webhook ingestion and HMAC verification
- [Type 4: S3 File Connector (Batch Sync)](data-sources/4-s3-files-synch.md) — Batch CSV, JSONL, and Parquet synchronization
- [Type 5: Mobile SDK Tracking](data-sources/5-mobile-sdk-tracking.md) — iOS/Android telemetry and mobile identifier ingestion
- [CDP Web SDK Tracking Reference](data-sources/cdp-web-sdk-tracking.md) & [Web SDK Demo Guide (VN)](data-sources/c360-web-sdk-demo-guide.vi.md)

## Customer Identity Resolution (CIR) & AI Personas

Metadata-driven identity stitching, consolidation survivorship rules, and persona vector resolution.

- [CIR Readiness & Improvement Notes](identity-resolution/CIR-improvement.md) — Matching taxonomy (exact, fuzzy, behavioral) vs. schema readiness
- [CIR Technical Slides (VN)](identity-resolution/CIR-Tech-Slides-VN.md) — Deep dive into `cdp_raw_profiles_stage`, `cdp_master_profiles`, and `CustomerIdentityResolver`
- [CIR Flow Diagrams](identity-resolution/images/) — Runtime and persona resolution Mermaid/SVG diagrams
- [11 AI Agent Types for Customer 360](ai-agent/agent-types-customer360.md) — Taxonomy of scoring, analytical, and activation AI agents

## Marketing, Segmentation & Campaign Orchestration

Omnichannel campaign workflows, human-approval governance gates, and channel delivery guides.

- [Marketing Workflow Specification](marketing/MARKETING-WORKFLOW.md) — Campaign approval, workflow version pinning, and execution states
- [Agentic Campaign Operations](marketing/agentic-campaign-ops..md) — AI-assisted campaign operations and human-in-the-loop governance
- [Email Marketing UAT Test Guide](marketing/email-marketing-uat-test.md) — End-to-end verification of email campaign delivery and tracking
- [Campaign Performance Slides (VN)](marketing/campaign-slide-VN.md) & [Marketing Diagrams](marketing/images/)

## Admin UI Page Guides (`docs/pages/`)

User and operator documentation for every screen in the Customer 360 Admin UI in both English and Vietnamese (indexed by the built-in documentation assistant):

- **Overview & Analytics:** [Overview](pages/overview.md) ([VN](pages/overview-vi.md)) · [Analytics](pages/analytics.md) ([VN](pages/analytics-vi.md))
- **Customer 360 & Identity:** [Profiles](pages/profiles.md) ([VN](pages/profiles-vi.md)) · [Profile Detail](pages/profile-detail.md) ([VN](pages/profile-detail-vi.md)) · [Attributes](pages/attributes.md) ([VN](pages/attributes-vi.md)) · [Data Sources](pages/datasources.md) ([VN](pages/datasources-vi.md))
- **Personas & Segments:** [Personas](pages/personas.md) ([VN](pages/personas-vi.md)) · [Persona Matched Profiles](pages/persona-matched-profiles.md) ([VN](pages/persona-matched-profiles-vi.md)) · [Segments](pages/segments.md) ([VN](pages/segments-vi.md)) · [Segment Detail](pages/segment-detail.md) ([VN](pages/segment-detail-vi.md))
- **Campaigns & Catalog:** [Campaigns](pages/campaigns.md) ([VN](pages/campaigns-vi.md)) · [Campaign Detail](pages/campaign-detail.md) ([VN](pages/campaign-detail-vi.md)) · [Campaign Editor](pages/campaign-editor.md) ([VN](pages/campaign-editor-vi.md)) · [Content & Products](pages/content-products.md) ([VN](pages/content-products-vi.md))
- **Administration:** [AI Agents](pages/agents.md) ([VN](pages/agents-vi.md)) · [Admin Users & RBAC](pages/admin-users.md) ([VN](pages/admin-users-vi.md))

## Action Plans & Feature Specifications (`docs/action-plans/`)

Engineering design documents and implementation plans for core platform capabilities:

- [LEO CDP v2.0 Beta Master Plan](action-plans/PLAN-LEOCDP-v2.0-beta.md)
- [Campaign Orchestration](action-plans/03_Campaign_Orchestration.md) & [Campaigns Dev Plan](action-plans/PLAN-CAMPAIGNS-DEV.md)
- [Agentic Email Marketing Flow](action-plans/AGENTIC-EMAIL-MARKETING-FLOW.md)
- [Agentic Zalo Marketing Flow](action-plans/AGENTIC-ZALO-MARKETING-FLOW.md) & [Zalo ZNS Optimization](action-plans/PLAN-ZALO-ZNS-OPTIMIZED.md)
- [Agentic Ad-Tech Audience Sync Flow](action-plans/AGENTIC-AD-TECH-FLOW.md)
- [Full Analytics with S3](action-plans/FULL-ANALYTICS-WITH-S3.md) & [S3 Analytics Baseline](action-plans/FULL-ANALYTICS-WITH-S3-BASELINE.md)
- [CRM Profiles API Improvement](action-plans/PLAN-CRM-PROFILES-API-IMPROVEMENT.md) · [Events API Improvement](action-plans/PLAN-EVENTS-API-IMPROVEMENT.md) · [Segments API Improvement](action-plans/PLAN-SEGMENTS-API-IMPROVEMENT.md)

## Operations & Deployment

Local development, Docker Compose profiles, production deployment, and PostgreSQL maintenance runbooks.

- [Docker Compose Guide](operations/DOCKER-COMPOSE-GUIDE.md) — `dev-docker-compose.yml`, `dev-no-sso-docker-compose.yml`, and full `docker-compose.yml`
- [Environment Configuration Notes](operations/environment-notes.md)
- [Production Deployment Runbook](operations/production-deployment.md)
- [Development Domain Configuration](operations/dev-example.com-configs.md)
- [Database Operations & Backup Tools](operations/database/README.md)

## Integration Q&A, Code Reviews & Research

- **Cross-Team Integration & Issues (`docs/issues/`):**
  - [Team CDP Questions & Technical Responses (SCOPE ↔ LeoCDP)](issues/team-cdp-questions.md)
  - [SCOPE Open Questions Ledger](issues/So_cau_hoi_mo_SCOPE_LeoCDP.md)
  - [Beta API Stale Deploy Incident Analysis](issues/2026-09-24-beta-api-stale-deploy-silent-noop.md)
- **Code Reviews (`docs/code-review/`):**
  - [Security & Architecture Review Index](code-review/README.md)
  - [Docs Chatbot Agent Implementation Plan](code-review/docs-chatbot-agent-implementation-plan.md) & [UAT Report](code-review/docs-chatbot-uat-deployment-report.md)
- **Research Papers & Notebooks:**
  - [Research Papers & Whitepapers](research-papers/) — Identity resolution ([SAE-CIR-EN](research-papers/SAE-CIR-EN.md), [SAE-CIR-VN](research-papers/SAE-CIR-VN.md)), [Persona as a Dynamic Vector](research-papers/human_persona_as_dynamic_vector_en.md), [Personalization](research-papers/personalization.md), and [Customer 360 Model Layer](research-papers/the-customer360-model-layer.md)
  - [Jupyter Notebooks](notebooks/) — RAG evaluation and persona roadmap experiments
- **Tooling:**
  - [CI/CD Telegram Chatbot Plan](tooling/cicd-telegram-chatbot-plan.md) · [Pandoc Notes](tooling/pandoc-notes/) · [LaTeX Template](tooling/template.tex)
