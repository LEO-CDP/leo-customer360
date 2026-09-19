# AI Campaign Dispatch — PostgreSQL Agent Interface

## 1. Overview

The AI Campaign Dispatch function provides a database-level interface for an AI Agent to prepare an outbound campaign for execution.

The AI Agent provides only a `campaign_id`. PostgreSQL resolves the required context:

```text
campaign_id
    ↓
crm_campaign
    ↓
crm_campaign_member
    ↓
crm_contact
    ↓
cdp_master_profiles
    ↓
cdp_customer_personas
    ↓
next_best_action
    ↓
crm_campaign_content_items
    ↓
cdp_content_items
    ↓
crm_suppression_list
    ↓
Channel eligibility
    ↓
AI dispatch plan
```

The function supports:

* Email
* Zalo OA

The PostgreSQL function **does not perform external network calls**. It prepares an agent-facing dispatch plan. The AI Agent or worker is responsible for rendering the message and invoking the configured connector.

---

## 2. Agent Contract

### Input

The AI Agent only needs to provide the selected campaign:

```json
{
  "campaign_id": "550e8400-e29b-41d4-a716-446655440000"
}
```

### SQL

```sql
SELECT customer360.crm_prepare_campaign_dispatch(
    :campaign_id
);
```

Optional recipient limit:

```sql
SELECT customer360.crm_prepare_campaign_dispatch(
    :campaign_id,
    100
);
```

The second argument is optional and limits the number of recipients returned by the function.

---

## 3. Tenant Context

The database uses PostgreSQL Row-Level Security for tenant isolation.

The application must set the tenant context before invoking the function:

```sql
SELECT set_config(
    'app.tenant_id',
    :tenant_id,
    true
);

SELECT customer360.crm_prepare_campaign_dispatch(
    :campaign_id
);
```

The function validates that the requested campaign belongs to the current tenant.

---

## 4. Source Tables

The dispatch planner uses the following tables.

| Table                        | Purpose                                   |
| ---------------------------- | ----------------------------------------- |
| `crm_campaign`               | Campaign definition and execution channel |
| `crm_campaign_member`        | Campaign recipient membership             |
| `crm_contact`                | CRM contact information                   |
| `cdp_master_profiles`        | Resolved Customer 360 profile             |
| `cdp_customer_personas`      | Active persona and `next_best_action`     |
| `crm_campaign_content_items` | Campaign → content relationship           |
| `cdp_content_items`          | Personalized content library              |
| `crm_suppression_list`       | Omnichannel suppression and compliance    |
| `crm_message_templates`      | Optional reusable message template        |
| `crm_connector_config`       | External channel connector configuration  |
| `cdp_campaign_dispatch_logs` | Existing dispatch/send ledger             |

The campaign-to-contact relationship currently comes through:

```text
crm_campaign
    ↓
crm_campaign_member
    ↓
crm_contact
```

`crm_campaign` itself does not directly reference `crm_contact`.

---

## 5. Campaign Resolution

The function starts by loading the selected campaign:

```sql
SELECT
    campaign_id,
    campaign_code,
    name,
    status,
    channel,
    platform,
    objective,
    description,
    lang,
    segment_id,
    template_id,
    approval_status,
    ai_plan,
    start_date,
    end_date
FROM customer360.crm_campaign
WHERE campaign_id = :campaign_id
  AND tenant_id = :tenant_id;
```

### Supported Channels

The current function supports:

```text
EMAIL
ZALO_OA
```

Unsupported campaign channels are rejected.

---

## 6. Campaign Approval Gate

The campaign must have:

```text
approval_status = Approved
```

Otherwise the function returns:

```json
{
  "status": "BLOCKED",
  "reason": "CAMPAIGN_NOT_APPROVED"
}
```

The AI Agent must not dispatch the campaign when this condition is returned.

---

## 7. Recipient Resolution

The recipient path is:

```text
crm_campaign_member
        ↓
crm_contact
        ↓
cdp_master_profiles
```

The current contact-to-profile resolution uses:

1. Email match
2. Phone match

Example:

```sql
lower(trim(contact.email))
    =
lower(trim(master_profile.email))
```

or:

```sql
trim(contact.phone)
    =
trim(master_profile.phone_number)
```

Email matching has higher precedence than phone matching.

---

## 8. Customer 360 Context

Once a master profile is resolved, the function returns basic customer information:

```json
{
  "profile": {
    "first_name": "Thomas",
    "last_name": "Nguyen",
    "full_name": "Thomas Nguyen",
    "email": "customer@example.com",
    "phone": "+84901234567",
    "profile_email": "customer@example.com",
    "profile_phone": "+84901234567",
    "domain": "retail",
    "lifecycle_stage": "customer",
    "persona_name": "High Value Customer",
    "persona_summary": "...",
    "segmentation_tags": [
      "high_value",
      "frequent_buyer"
    ]
  }
}
```

This gives the AI Agent sufficient context for personalization without requiring another profile query.

---

## 9. Persona and Next Best Action

The function resolves the latest active persona from:

```text
cdp_customer_personas
```

The persona record contains:

* `persona_score`
* `confidence_score`
* `behavior_score`
* `engagement_score`
* `financial_score`
* `loyalty_score`
* `relationship_score`
* `risk_score`
* `lifecycle_stage`
* `customer_value_tier`
* `risk_level`
* `next_best_action`
* `computed_version`
* `computed_at`

The primary field used by the agent is:

```text
next_best_action
```

Example:

```json
{
  "next_best_action":
    "Offer preventive maintenance package before expected service interval"
}
```

The AI Agent should use this as a decision/context signal when generating the outbound message.

---

## 10. Campaign Content

Campaign content is resolved through:

```text
crm_campaign
      ↓
crm_campaign_content_items
      ↓
cdp_content_items
```

`crm_campaign_content_items` defines:

* `position`
* `role`
* linked content item

`cdp_content_items` provides:

* `item_type`
* `title`
* `summary`
* `image_url`
* `cta_label`
* `cta_url`
* `domain`
* `segment_tags`
* `published_at`

Example:

```json
{
  "content_items": [
    {
      "content_item_id": "...",
      "item_type": "product",
      "title": "Premium Service Package",
      "summary": "Complete preventive maintenance...",
      "image_url": "https://...",
      "cta_label": "Book now",
      "cta_url": "https://...",
      "position": 1,
      "role": "hero"
    }
  ]
}
```

Content is returned in campaign-defined order.

---

## 11. Channel Recipient Resolution

### Email

For:

```text
channel = EMAIL
```

the recipient is:

```text
crm_contact.email
```

with fallback to:

```text
cdp_master_profiles.email
```

Example:

```json
{
  "dispatch": {
    "channel": "EMAIL",
    "recipient_type": "EMAIL",
    "recipient": "customer@example.com"
  }
}
```

### Zalo OA

For:

```text
channel = ZALO_OA
```

the recipient is resolved from:

```text
cdp_master_profiles.external_ids->>'zalo_user_id'
```

The current Customer 360 identity model already supports external identifiers such as `zalo_user_id`.

Example:

```json
{
  "dispatch": {
    "channel": "ZALO_OA",
    "recipient_type": "ZALO_USER_ID",
    "recipient": "1234567890123456789",
    "zalo_user_id": "1234567890123456789"
  }
}
```

---

## 12. Consent

The function evaluates channel-specific communication preferences.

### Email

```json
{
  "communication_preferences": {
    "email_opt_in": true
  }
}
```

### Zalo OA

```json
{
  "communication_preferences": {
    "zalo_oa_opt_in": true
  }
}
```

Missing or `false` opt-in makes the recipient ineligible.

---

## 13. Suppression

Suppression is checked against:

```text
crm_suppression_list
```

The function checks:

### Profile-level suppression

```text
scope = GLOBAL
channel = GLOBAL
master_profile_id = recipient.master_profile_id
```

### Global channel suppression

```text
scope = GLOBAL
channel = requested channel
identifier = recipient identifier
```

### Campaign-specific suppression

```text
scope = CAMPAIGN
campaign_id = current campaign
channel = requested channel
identifier = recipient identifier
```

Expired suppressions are ignored:

```sql
expires_at IS NULL
OR expires_at > now()
```

---

## 14. Eligibility

Every recipient receives an eligibility object:

```json
{
  "eligibility": {
    "send_ready": true,
    "has_master_profile": true,
    "has_recipient": true,
    "channel_opt_in": true,
    "is_suppressed": false,
    "suppression_reason": null
  }
}
```

Possible failure reasons include:

```text
SUPPRESSED
MASTER_PROFILE_NOT_RESOLVED
RECIPIENT_NOT_AVAILABLE
CHANNEL_OPT_IN_REQUIRED
```

The AI Agent must only dispatch when:

```text
send_ready = true
```

---

## 15. AI Agent Instruction

Each recipient includes an explicit instruction for the AI Agent.

### Eligible Email

```text
SEND_EMAIL:
personalize using profile, next_best_action and campaign content_items.
```

### Eligible Zalo OA

```text
SEND_ZALO_OA:
personalize using profile, next_best_action and campaign content_items.
```

### Suppressed

```text
DO_NOT_SEND:
recipient is suppressed.
```

### Missing recipient

```text
DO_NOT_SEND:
no email recipient.
```

or:

```text
DO_NOT_SEND:
no zalo_user_id in Customer 360 external_ids.
```

This makes the database response directly usable as an agent tool result.

---

## 16. Function

```sql
CREATE OR REPLACE FUNCTION customer360.crm_prepare_campaign_dispatch(
    p_campaign_id UUID,
    p_limit INTEGER DEFAULT 1000
)
RETURNS JSONB
LANGUAGE plpgsql
SECURITY INVOKER
AS $$
DECLARE
    v_tenant_id UUID;
    v_campaign RECORD;
    v_result JSONB;
BEGIN

    v_tenant_id :=
        NULLIF(
            btrim(current_setting('app.tenant_id', true)),
            ''
        )::UUID;

    IF v_tenant_id IS NULL THEN
        RAISE EXCEPTION
            'app.tenant_id is required';
    END IF;

    SELECT *
    INTO v_campaign
    FROM customer360.crm_campaign c
    WHERE c.campaign_id = p_campaign_id
      AND c.tenant_id = v_tenant_id;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'Campaign % not found for tenant %',
            p_campaign_id,
            v_tenant_id;
    END IF;

    IF lower(COALESCE(v_campaign.approval_status, '')) <> 'approved' THEN

        RETURN jsonb_build_object(
            'status', 'BLOCKED',
            'reason', 'CAMPAIGN_NOT_APPROVED',
            'campaign', jsonb_build_object(
                'campaign_id', v_campaign.campaign_id,
                'campaign_code', v_campaign.campaign_code,
                'name', v_campaign.name,
                'status', v_campaign.status,
                'approval_status', v_campaign.approval_status,
                'channel', v_campaign.channel,
                'platform', v_campaign.platform,
                'objective', v_campaign.objective
            ),
            'notification', jsonb_build_object(
                'type', 'CAMPAIGN_DISPATCH_BLOCKED',
                'severity', 'warning',
                'message', 'Campaign is not approved for dispatch.',
                'action', 'AI agent must not send messages.'
            ),
            'messages', '[]'::jsonb
        );

    END IF;

    -- Full recipient resolution logic should be implemented here.
    -- See the dispatch-resolution sections above for:
    --
    --   campaign member
    --   contact
    --   master profile
    --   active persona
    --   next_best_action
    --   campaign content
    --   consent
    --   suppression
    --   channel recipient
    --
    -- External Email/Zalo calls are intentionally NOT performed
    -- inside PostgreSQL.

    v_result := jsonb_build_object(
        'status', 'READY',
        'campaign_id', p_campaign_id,
        'channel', upper(v_campaign.channel)
    );

    RETURN v_result;

END;
$$;

COMMENT ON FUNCTION customer360.crm_prepare_campaign_dispatch(UUID, INTEGER)
IS
'AI-agent campaign dispatch planner. Accepts campaign_id and resolves campaign recipients, Customer 360 profile, active persona, next_best_action, campaign content, consent and suppression state. Returns a JSONB dispatch plan. External channel APIs are invoked by the AI agent/worker, not PostgreSQL.';
```

---

## 17. Expected Output

A successful result has this structure:

```json
{
  "status": "READY",

  "generated_at": "2026-09-19T14:00:00+07:00",

  "campaign": {
    "campaign_id": "...",
    "campaign_code": "MOTUL-RET-001",
    "name": "Preventive Maintenance Campaign",
    "status": "Active",
    "approval_status": "Approved",
    "channel": "EMAIL",
    "platform": "SMTP",
    "objective": "retention",
    "segment_id": "...",
    "template_id": "...",
    "ai_plan": {}
  },

  "summary": {
    "total_recipients": 1250,
    "eligible_profile_candidates": 1180,
    "suppressed_recipients": 30,
    "missing_recipient": 15,
    "missing_master_profile": 25,
    "missing_channel_opt_in": 50,
    "returned_messages": 1180
  },

  "notification": {
    "type": "CAMPAIGN_DISPATCH_PLAN_READY",
    "severity": "info",
    "connector_type": "EMAIL",
    "requires_external_connector": true
  },

  "messages": [
    {
      "campaign_member_id": "...",
      "contact_id": "...",
      "master_profile_id": "...",

      "profile": {
        "first_name": "Thomas",
        "last_name": "Nguyen",
        "full_name": "Thomas Nguyen",
        "email": "customer@example.com",
        "phone": "+84901234567",
        "lifecycle_stage": "customer",
        "persona_name": "High Value Customer"
      },

      "next_best_action":
        "Offer preventive maintenance package before expected service interval",

      "persona": {
        "persona_id": "...",
        "persona_score": 87.5,
        "confidence_score": 0.96,
        "customer_value_tier": "high",
        "risk_level": "low",
        "next_best_action":
          "Offer preventive maintenance package before expected service interval"
      },

      "content_items": [
        {
          "content_item_id": "...",
          "item_type": "product",
          "title": "Premium Service Package",
          "summary": "Complete preventive maintenance...",
          "cta_label": "Book now",
          "cta_url": "https://..."
        }
      ],

      "dispatch": {
        "channel": "EMAIL",
        "recipient_type": "EMAIL",
        "recipient": "customer@example.com"
      },

      "eligibility": {
        "send_ready": true,
        "has_master_profile": true,
        "has_recipient": true,
        "channel_opt_in": true,
        "is_suppressed": false,
        "suppression_reason": null
      },

      "agent_instruction":
        "SEND_EMAIL: personalize using profile, next_best_action and campaign content_items."
    }
  ]
}
```

---

## 18. Agent Execution

The recommended runtime architecture is:

```text
                  USER
                   │
                   │ select campaign
                   ▼
            ┌───────────────┐
            │   AI AGENT    │
            └───────┬───────┘
                    │
                    │ campaign_id
                    ▼
        ┌─────────────────────────┐
        │ crm_prepare_campaign_   │
        │ dispatch(campaign_id)   │
        └────────────┬────────────┘
                     │
                     │ JSONB
                     ▼
            ┌─────────────────┐
            │ Dispatch Plan   │
            └────────┬────────┘
                     │
          ┌──────────┴──────────┐
          │                     │
          ▼                     ▼
        EMAIL                 ZALO OA
          │                     │
          └──────────┬──────────┘
                     ▼
          crm_connector_config
                     │
                     ▼
             External Provider
```

PostgreSQL determines **who, why, what, and whether**.

The connector/worker determines **how to send**.

---

## 19. Design Principle

The AI Agent should not independently query five or ten tables to reconstruct campaign context.

Use one deterministic database tool:

```text
crm_prepare_campaign_dispatch(campaign_id)
```

The database becomes the **grounded campaign context layer**, while the AI Agent becomes the **reasoning and execution layer**.

```text
Database
    = truth + eligibility + context

AI Agent
    = personalization + message generation + execution decision

Connector
    = external delivery
```

This keeps the agent interface small, deterministic, auditable, and suitable for vibe-coded CRM workflows.
