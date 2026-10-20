# LEO CDP — UAT Testing Plan: Email Marketing Workflows

## Local UAT Testing Technical Specification

**Document type:** Technical UAT Guide\
**System:** LEO CDP — Customer Data Platform\
**Module:** Marketing Campaign & Workflow Engine\
**Environment:** Local development / Docker\
**Email testing:** Mailpit\
**Status:** Local Mailpit integration implemented; UAT execution pending

## 1. Purpose

Define the local User Acceptance Testing (UAT) procedure for the LEO CDP Marketing Workflow, covering audience selection, workflow design, approvals, campaign execution, email capture, terminal states, and asynchronous child-run observability.

Mailpit acts as a local SMTP capture server. It allows outgoing emails to be inspected without delivery to real recipients or reliance on an external SMTP provider.

### 1.1 Scope

- Customer segment selection and profile loading.
- Workflow persistence, version pinning, and atomic saves.
- Human approval of workflow versions and campaigns.
- Campaign activation and execution.
- Per-profile workflow evaluation and reachable action-node execution.
- Email action validation using Mailpit.
- Campaign completion and failure states.
- Asynchronous audience-sync child runs.
- Logs, troubleshooting, and acceptance criteria.

### 1.2 Reference documentation

- LEO CDP: [https://github.com/LEO-CDP/leo-customer360/blob/main/docs/marketing/MARKETING-WORKFLOW.md](https://github.com/LEO-CDP/leo-customer360/blob/main/docs/marketing/MARKETING-WORKFLOW.md)
- Mailpit Docker images: [https://hub.docker.com/r/axllent/mailpit](https://hub.docker.com/r/axllent/mailpit)
- Mailpit repository: [https://github.com/axllent/mailpit](https://github.com/axllent/mailpit)
- Mailpit runtime options: [https://github.com/axllent/mailpit/blob/develop/docs/configuration/runtime-options.md](https://github.com/axllent/mailpit/blob/develop/docs/configuration/runtime-options.md)

---

## 2. Architecture

```
Marketing UI
    |
    v
Campaign & Workflow API
    |
    +---- Campaign Storage
    |
    +---- Workflow Storage
    |
    v
Campaign Approval & Activation
    |
    v
campaign_workflow_job
    |
    v
Load Selected Profiles
    |
    v
Evaluate Workflow Per Profile
    |
    v
Execute Reachable Action Nodes
    |
    v
Email Action Handler
    |
    v
Mailpit SMTP :1025
    |
    v
Mailpit Web UI :8025

Campaign Execution
    |
    +---- Completed / Failed
    |
    +---- Asynchronous Audience-Sync Child Runs
```

The diagram describes the logical integration. Actual API boundaries, persistence mechanisms, and job execution depend on the current LEO CDP implementation.

---

## 3. Prerequisites

Before UAT begins, ensure that:

- Docker Engine and Docker Compose v2 are available.
- LEO CDP and its required dependencies are running.
- A designated test tenant is available.
- At least one segment contains synthetic test profiles.
- The marketing workflow module is enabled.
- A user with appropriate permissions can create and approve campaigns.
- The email action handler supports configurable SMTP settings.
- Testers can inspect campaign execution logs and run identifiers.

### 3.1 Prerequisites Traceability Matrix

The following matrix establishes a traceable relationship between each prerequisite, its validation method, the evidence required, and the UAT cases that depend on it.

**Status definitions:** Pending = not checked; Pass = verified; Fail = prerequisite is not satisfied; Blocked = validation cannot proceed.

| ID    | Prerequisite                   | Validation method                                       | Expected evidence / acceptance condition                                 | Dependent tests               | Status  |
| ----- | ------------------------------ | ------------------------------------------------------- | ------------------------------------------------------------------------ | ----------------------------- | ------- |
| PR-01 | Docker Engine                  | Run `docker --version` and `docker info`                | Docker daemon responds successfully                                      | UAT-01, UAT-02                | Pending |
| PR-02 | Docker Compose v2              | Run `docker compose version`                            | Compose v2 is available                                                  | UAT-01, UAT-18                | Pending |
| PR-03 | LEO CDP application            | Check service health and application access             | Required application endpoints respond                                   | UAT-03–UAT-17                 | Pending |
| PR-04 | Required persistence services  | Verify database connectivity and migrations             | Required tables and schema are accessible                                | UAT-04–UAT-06, UAT-15–UAT-17  | Pending |
| PR-05 | Test tenant                    | Open or query the designated tenant                     | Test tenant exists and is isolated                                       | UAT-03–UAT-20                 | Pending |
| PR-06 | Customer segment               | Query or open the selected segment                      | Segment contains the expected synthetic profiles                         | UAT-03, UAT-10–UAT-14         | Pending |
| PR-07 | Profile test data              | Inspect profile IDs and required attributes             | Eligible, ineligible, and invalid-recipient cases are represented        | UAT-10–UAT-14                 | Pending |
| PR-08 | Workflow engine                | Create and validate a test workflow                     | Conditions and action nodes can be configured                            | UAT-04, UAT-06, UAT-11–UAT-14 | Pending |
| PR-09 | Campaign persistence           | Inspect campaign and workflow records                   | Both configurations can be saved and retrieved                           | UAT-04, UAT-05                | Pending |
| PR-10 | Atomic-save behavior           | Inject a controlled persistence failure                 | No inconsistent partial state remains                                    | UAT-05                        | Pending |
| PR-11 | Workflow version pinning       | Inspect workflow version references                     | Campaign references the approved version                                 | UAT-06                        | Pending |
| PR-12 | Human approval controls        | Test workflow and campaign approval permissions         | Required review cannot be bypassed                                       | UAT-06–UAT-08                 | Pending |
| PR-13 | Campaign lifecycle             | Verify allowed state transitions                        | Review, approval, activation, running, and terminal states follow policy | UAT-07–UAT-09, UAT-15–UAT-16  | Pending |
| PR-14 | Campaign job runner            | Execute a controlled test job                           | `campaign_workflow_job` starts and produces an inspectable run           | UAT-10–UAT-17                 | Pending |
| PR-15 | SMTP configuration             | Inspect the effective email configuration               | SMTP host, port, TLS, and authentication match the test environment      | UAT-02, UAT-11–UAT-14         | Pending |
| PR-16 | Mailpit availability           | Check container health and open the Web UI              | Mailpit is running; UI is accessible on port 8025                        | UAT-01, UAT-02                | Pending |
| PR-17 | SMTP connectivity              | Send a standalone SMTP smoke-test email                 | SMTP accepts the message and Mailpit displays it                         | UAT-02                        | Pending |
| PR-18 | Email template                 | Render a sample template using a test profile           | Expected personalization fields resolve correctly                        | UAT-13                        | Pending |
| PR-19 | Execution logs                 | Trigger a controlled run and inspect logs               | Campaign, job, profile, and action outcomes can be correlated            | UAT-10–UAT-17, UAT-19         | Pending |
| PR-20 | Asynchronous child-run support | Trigger an audience-sync action if implemented          | Child run can be identified and inspected independently                  | UAT-17                        | Pending |
| PR-21 | Retry and idempotency policy   | Inspect implementation and execute a controlled retry   | Duplicate-side-effect behavior is documented and tested                  | UAT-19                        | Pending |
| PR-22 | Local delivery isolation       | Verify SMTP configuration and inspect captured messages | No external SMTP relay or real-recipient delivery is enabled             | UAT-02, UAT-20                | Pending |
| PR-23 | Evidence collection            | Prepare test records and screenshot/log storage         | Test results can be traced to campaign and run IDs                       | All UAT cases                 | Pending |

### 3.2 Prerequisite exit criteria

UAT execution may begin when all mandatory prerequisites are marked **Pass**.

- A failed prerequisite must be resolved before dependent tests proceed.
- A blocked prerequisite must be documented with its reason and impact.
- A prerequisite that depends on an unimplemented feature must not be reported as passed.
- Evidence should identify the build or Git commit used for testing.

The matrix is a living test artifact. Update its status and evidence references during test execution.

---

## 4. Start and verify Mailpit

Mailpit is included in both development Compose variants:
`dev-docker-compose.yml` (SSO) and `dev-no-sso-docker-compose.yml`. Do not
create a separate Mailpit Compose project; using the integrated service keeps
the host port mapping, health status, and persistent inbox consistent with
`dev-c360.sh`.

From the repository root, run the normal startup:

```bash
bash ./dev-c360.sh
```

`dev-c360.sh` chooses the Compose variant from `SSO_LOGIN`, starts Mailpit, and
waits for its `/mailpit readyz` healthcheck before it starts/restarts the host
API and backend. Startup fails with container logs if Mailpit does not become
healthy.

Access:

- Mailpit Web UI: [http://127.0.0.1:8025](http://127.0.0.1:8025)
- SMTP host from host-run services: `127.0.0.1`
- SMTP port: `1025`

Check the service directly:

```bash
docker compose -f dev-docker-compose.yml ps mailpit
docker compose -f dev-docker-compose.yml logs --tail=100 mailpit
```

When `SSO_LOGIN=false`, replace `dev-docker-compose.yml` with
`dev-no-sso-docker-compose.yml`. The same service/container name and ports are
used by both variants, so do not run the two Compose files simultaneously.

Mailpit binds to loopback by default. Captured messages persist in the named
`customer360-mailpitdata` volume across container restarts. `dev-c360.sh reset`
uses `down -v` and deletes this volume along with the local development data.

## 5. Configure LEO CDP SMTP

The repository-supported settings are:

```dotenv
EMAIL_DISPATCH_ADAPTER=smtp
SMTP_HOST=127.0.0.1
SMTP_PORT=1025
SMTP_USERNAME=
SMTP_PASSWORD=
SMTP_USE_TLS=false
EMAIL_FROM_ADDRESS=no-reply@leocdp.local
EMAIL_FROM_NAME=LEOCDP
```

These settings are present in the root `.env.example`. `dev-c360.sh` creates
`.env` from that example if absent and adds missing keys without overwriting
existing local values. For an existing `.env`, set the Mailpit values there;
the values in `.env.example` do not replace customized values automatically.
Do not put production SMTP credentials in this local configuration.

### 5.1 Which email path uses these settings?

- `EMAIL_DISPATCH_ADAPTER` and the `SMTP_*` variables are the environment
  fallback for both `customer360-backend/email_engine` and campaign workflow
  email actions. An active tenant `crm_connector_config` EMAIL row takes
  precedence.
- With no active tenant connector, `EMAIL_DISPATCH_ADAPTER=smtp` routes through
  the Mailpit settings above. If neither a tenant connector nor an explicit
  environment adapter is configured, campaign workflow email actions are
  recorded as skipped.

For either email path, verify the effective provider is Mailpit before running
UAT. If the tenant has an active EMAIL connector, set that connector to SMTP
host `127.0.0.1`, port `1025`, TLS disabled, and empty credentials, or remove
the row from the local test tenant. A database connector always takes
precedence over `.env`.

Mailpit's local SMTP listener is plaintext by default. `SMTP_USE_TLS=false`
and empty username/password are intentional for this loopback-only development
setup. Do not expose Mailpit ports beyond loopback or use these settings for a
production relay.

---

## 6. SMTP Smoke Test

Before campaign execution, verify that Mailpit captures a standalone message.

```
curl --url smtp://127.0.0.1:1025 \
  --mail-from noreply@leo-cdp.local \
  --mail-rcpt uat-recipient@example.test \
  --upload-file - <<'EOF'
From: LEO CDP UAT <noreply@leo-cdp.local>
To: UAT Recipient <uat-recipient@example.test>
Subject: LEO CDP SMTP Smoke Test
Content-Type: text/plain; charset=UTF-8

This is a local SMTP capture test for LEO CDP.
No external email delivery is expected.
EOF
```

Acceptance criteria:

1. The SMTP client completes successfully.
2. The message appears in Mailpit.
3. Sender, recipient, subject, and body are correct.
4. The message is not delivered to an external mailbox.

If the smoke test fails, resolve the problem before running campaign UAT.

---

## 7. Workflow Under Test

### Phase 1 — Plan and design

1. Select a segment.
2. Design a workflow containing conditions and actions.
3. Save the campaign and workflow atomically.

Expected behavior: The campaign and workflow are persisted consistently. A failed atomic save must not leave an inconsistent partial configuration.

### Phase 2 — Governance and approval

4. Approve the pinned workflow version.
5. Submit the campaign for review.
6. Approve the campaign.
7. Activate the campaign.

Expected behavior: Execution uses the approved pinned workflow version. Agent-proposed changes require the specified human review before approval.

### Phase 3 — Execution

8. Mark the campaign `Running`.
9. Execute `campaign_workflow_job`.
10. Load the selected profiles.
11. Evaluate workflow logic per profile.
12. Execute reachable action nodes.

Expected behavior: Only actions reachable under the evaluated workflow path execute. Email actions submit messages to Mailpit.

### Phase 4 — Completion and observability

13. Mark the campaign `Completed` or `Failed` according to the execution policy.
14. Inspect asynchronous audience-sync child runs separately.

Expected behavior: Campaign terminal states follow the implemented execution policy. Child runs remain independently observable and must not be assumed to share the parent campaign's status.

---

## 8. UAT Test Data

Use synthetic profiles in an isolated tenant.

| Profile | Email                   | Purpose                                           |
| ------- | ----------------------- | ------------------------------------------------- |
| P001    | `uat-p001@example.test` | Eligible; email action reachable                  |
| P002    | `uat-p002@example.test` | Ineligible; email action skipped                  |
| P003    | `uat-p003@example.test` | Eligible; validate personalization                |
| P004    | `uat-p004@example.test` | Missing or invalid email; validate error handling |

Example workflow:

```
Start
  |
  v
Check profile eligibility
  |
  +-- Not eligible --> End
  |
  +-- Eligible
        |
        v
     Send Email
        |
        v
       End
```

Example email content:

```
Subject: LEO CDP UAT — Hello {{ first_name }}

Hello {{ first_name }},

This is a local UAT message for profile {{ profile_id }}.

Campaign: {{ campaign_name }}
```

The template syntax is illustrative. Use the actual syntax supported by the LEO CDP template engine.

---

## 9. UAT Test Cases

| ID     | Scenario                  | Expected result                                                 |
| ------ | ------------------------- | --------------------------------------------------------------- |
| UAT-01 | Start Mailpit             | Container runs; UI is accessible                                |
| UAT-02 | SMTP smoke test           | Message appears in Mailpit                                      |
| UAT-03 | Select segment            | Correct segment is associated with the campaign                 |
| UAT-04 | Save workflow             | Campaign and workflow persist consistently                      |
| UAT-05 | Atomic-save failure       | No inconsistent partial state remains                           |
| UAT-06 | Approve pinned version    | Campaign references the approved workflow version               |
| UAT-07 | Submit campaign           | Campaign enters the expected review state                       |
| UAT-08 | Activate before approval  | Activation is rejected or blocked according to policy           |
| UAT-09 | Approve and activate      | Campaign becomes eligible for execution                         |
| UAT-10 | Execute campaign job      | Job starts and loads intended profiles                          |
| UAT-11 | Eligible profiles         | Reachable email actions execute                                 |
| UAT-12 | Ineligible profile        | Email action is skipped                                         |
| UAT-13 | Personalization           | Profile-specific values render correctly                        |
| UAT-14 | Invalid recipient         | Error or skip behavior follows the defined policy               |
| UAT-15 | Successful terminal state | Campaign becomes `Completed` under the defined success criteria |
| UAT-16 | Execution failure         | Campaign becomes `Failed` under the defined failure criteria    |
| UAT-17 | Async child run           | Audience-sync child run is independently inspectable            |
| UAT-18 | Restart Mailpit           | Persisted messages remain available                             |
| UAT-19 | Retry failed job          | Duplicate-side-effect behavior is verified                      |
| UAT-20 | Delivery isolation        | No test message reaches an external recipient                   |

### Additional assertions

**Atomic persistence:** Inject a controlled persistence failure and verify rollback or equivalent consistency behavior.

**Pinned version:** Modify a workflow after approving an earlier version. Confirm the campaign still references its approved version unless a new version is explicitly reviewed and approved.

**Human approval:** Confirm that agent-proposed changes cannot bypass mandatory human review.

**Reachable actions:** Test branching conditions and verify that only reachable action nodes execute.

**Idempotency:** Retry a controlled job and verify duplicate behavior against the documented guarantee. If exactly-once execution is not implemented, record the limitation.

**Asynchronous child runs:** Trigger an audience-sync action if supported. Inspect the child run's status, timestamps, and logs independently of the parent campaign.

---

## 10. Test Execution Procedure

1. Start the required LEO CDP services.
2. Start Mailpit and clear old messages if required.
3. Verify SMTP connectivity.
4. Create a campaign with a unique UAT identifier.
5. Select the test segment and design the workflow.
6. Save the campaign and workflow.
7. Approve the pinned workflow version.
8. Submit the campaign for review.
9. Approve and activate the campaign.
10. Trigger `campaign_workflow_job`.
11. Inspect job logs and profile-level outcomes.
12. Inspect the Mailpit inbox.
13. Compare captured messages against expected recipients, content, and action counts.
14. Verify the terminal campaign status.
15. Inspect asynchronous child runs separately.
16. Record evidence and assign pass/fail results.

### 10.1 Evidence to collect

- Test ID and execution timestamp.
- Build or Git commit.
- Tenant, campaign, segment, and workflow identifiers.
- Pinned workflow version.
- Approval and activation state.
- Campaign job or run identifier.
- Expected and actual profile counts.
- Expected and actual email submissions.
- Mailpit message details or screenshots.
- Errors, logs, and child-run identifiers.
- Pass/fail result and defect reference.

Use synthetic data in local UAT evidence.

---

## 11. Troubleshooting

| Symptom                           | Likely cause                                   | Diagnostic action                                  |
| --------------------------------- | ---------------------------------------------- | -------------------------------------------------- |
| UI does not open                  | Container stopped or port conflict             | Check `docker compose ps` and logs                 |
| SMTP connection refused           | Wrong host/port or service unavailable         | Check container and network reachability           |
| `mailpit` hostname unresolved     | Services do not share a Docker network         | Attach both services to a common network           |
| No email after campaign execution | Action unreachable, disabled, or misconfigured | Inspect workflow and SMTP logs                     |
| Incorrect message count           | Branching, retries, or duplicate execution     | Correlate job, profile, and action records         |
| Empty personalization             | Template or profile mapping mismatch           | Inspect resolved template inputs                   |
| Campaign remains `Running`        | Stalled job or failed status update            | Inspect worker health and run logs                 |
| Campaign fails but messages exist | Partial execution before an error              | Compare action outcomes with terminal-state policy |
| Child run remains pending         | Still processing or failed independently       | Inspect child-run status and logs                  |
| Messages disappear after restart  | Persistence misconfigured                      | Verify `MP_DATABASE` and the mounted volume        |

Diagnostic commands:

```
docker compose ps
docker compose logs --tail=200 mailpit
docker inspect leo-cdp-mailpit
curl -I http://localhost:8025
nc -vz 127.0.0.1 1025
```

---

## 12. Cleanup

Stop Mailpit:

```
docker compose down
```

Clear captured messages using the supported Mailpit interface or API. Stop Mailpit before manually resetting its database.

Reset campaign and test-profile data separately using the supported LEO CDP test-data procedure.

---

## 13. Acceptance Criteria

- All mandatory prerequisites are marked Pass.
- Mailpit starts reliably and captures SMTP messages.
- Campaign and workflow persistence maintains required consistency.
- Approved workflow versions remain pinned.
- Agent-proposed changes cannot bypass required human review.
- Campaign approval and activation transitions behave correctly.
- The campaign job loads the intended profiles.
- Profile conditions determine which action nodes execute.
- Email actions generate the expected messages in Mailpit.
- Personalization values render correctly.
- Error handling and terminal states follow the defined policy.
- Retry and duplicate-side-effect behavior is tested.
- Asynchronous audience-sync child runs can be inspected independently.
- Logs and evidence support failure diagnosis.
- No test email is delivered to an external recipient.

A test case must be marked **Blocked** if the feature is unavailable or cannot be exercised. Untested behavior must not be marked Pass.

---

## 14. UAT Completion Report

| Field                 | Value          |
| --------------------- | -------------- |
| Build / Git commit    | To be recorded |
| Environment           | Local Docker   |
| Test tenant           | To be recorded |
| Campaign ID           | To be recorded |
| Workflow ID / version | To be recorded |
| Prerequisites passed  | To be recorded |
| Test cases passed     | To be recorded |
| Test cases failed     | To be recorded |
| Test cases blocked    | To be recorded |
| Defects               | To be recorded |
| UAT decision          | Pending        |

**PASS:** All mandatory acceptance criteria are satisfied.

**FAIL:** One or more mandatory acceptance criteria fail.

**BLOCKED:** Critical prerequisites or implementation capabilities prevent valid testing.

---

## 15. Implementation Notes

This document defines the UAT process and expected behavior; it does not claim that every capability is already implemented in the LEO CDP repository.

Before using this guide as a release gate, verify the actual SMTP configuration keys, campaign lifecycle states, job runner interfaces, workflow version model, retry/idempotency policy, and child-run observability against the current source code.

The central validation chain is:

**Approved campaign → pinned workflow → profile evaluation → reachable action → captured email → observable execution outcome.**

Add explicit test case proceduresSeparate implemented from illustrative behavior