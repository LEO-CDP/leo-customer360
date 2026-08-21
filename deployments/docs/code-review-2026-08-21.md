# Code Review — `deployments/`

**Date:** 2026-08-21
**Scope:** all code under `deployments/` — shell scripts (`deploy-all.sh`, `set-domain.sh`, `*/deploy*.sh`, `run-sql.sh`, `seed_data.sh`, `lib/*.sh`), Terraform (`*/*.tf`), and Python bootstrap scripts. Excluded: `.terraform/`, `*.tfstate*`, `terraform.tfstate.d/`, `*.tfvars`/`.env` (secrets, not opened), SQL files, `Caddyfile`.
**Method:** independent fresh-eyes pass over every in-scope file, then each load-bearing finding curated and spot-verified against the source (`file:line` confirmed for H1, M4, M6, L2, and others).

> **Security scan note:** every file was checked for AI/agent-directed instruction text (prompt injection). **None found** — the only `llm` hits are a legitimate `data-view-for-llm.sql` reference. (The earlier `/code-review` run that surfaced a `java-thetailor-review` "system-reminder" was a skill-router artifact in that agent's output, **not** content planted in the repo.)

---

## Overall assessment

A carefully written, well-commented deployment layer. The four SSH/container app-deploy scripts share a consistent structure; Terraform modules use `precondition`s + workspaces for env isolation; secrets are kept out of committed files and written to `chmod 600` env-files on the VMs. Core logic is sound. The material weaknesses are: (1) an **insecure-by-default OAuth wildcard redirect**, (2) a pervasive pattern of **passing secrets as command-line arguments** (base64 is obfuscation, not encryption), and (3) **Terraform state gaps** — no locking on the S3 backend and `load_balancer` having no remote backend despite being an orchestrated IaC step. Everything else is robustness/consistency polish.

Severity counts: **1 High · 5 Medium · 7 Low · 4 Nit.**

---

## High

### H1 — Wildcard OAuth redirect URI shipped by default
`sso/bootstrap-realm.py:26`, `:90` · `deploy-all.sh:157`
- **Issue:** If `sso_redirect_uris` is not set in the overlay, the realm bootstrap registers `redirectUris: ["*"]` for the confidential `customer360-api` client (standard flow + direct-access grants enabled), with `webOrigins: ["+"]`. A `*` redirect URI is the canonical OAuth misconfiguration (auth-code interception / open redirect); `webOrigins:["+"]` broadens CORS to every registered origin. The client secret partially mitigates code redemption, but this is insecure-by-default and flagged by Keycloak and every scanner.
- **Fix:** Require an explicit, non-wildcard redirect list. In `deploy-all.sh:157` drop the `:-*` fallback and `die` if `sso_redirect_uris` is unset; in `bootstrap-realm.py` reject `"*"`/empty for `REDIRECT_URIS`. Scope `webOrigins` to the real public origin.

---

## Medium

### M2 — Secrets passed as command-line arguments to `ssh`/`bash -s`
`server/deploy-api.sh:115` · `server/deploy-backend.sh:71` · `server/seed_data.sh:70` · `cache/deploy.sh:76` · `sso/deploy-sso.sh:89-91` · `ads-server/deploy-ads.sh:118` · `frontend/deploy-frontend.sh:88` · `postgres/run-sql.sh:70-74` · `monitoring/deploy-monitoring.sh:100,137-146`
- **Issue:** DB master password, `GHCR_TOKEN`/`GITHUB_TOKEN`, Keycloak admin + client secrets, Portainer admin password, and oauth2 secrets are base64-encoded and passed as **argv** to `ssh` → `bash -s`. base64 is trivially reversible, and argv is world-readable via `ps -ef` / `/proc/<pid>/cmdline` on both the deploy host and the target VM for the process lifetime. Real exposure on a shared CI runner or multi-tenant VM. (The GHCR-token argv on the app scripts was introduced with the CD work; the DB-password pattern predates it.)
- **Fix:** Send secret material on a separate **stdin** channel the remote reads with `read` before running the body, or via `ssh -o SetEnv` with `AcceptEnv` on the box — not positional args.

### M3 — oauth2-proxy secrets in container argv + cookie over plain HTTP
`monitoring/deploy-monitoring.sh:214-215`
- **Issue:** The OIDC client secret and cookie secret sit in the **running container's** argv for its whole lifetime (readable via `docker inspect`/`ps`). `--cookie-secure=false` sends the session cookie over the plain-HTTP L4-LB path — interceptable/replayable (dashboard session hijack).
- **Fix:** Pass via `--client-secret-file` / env-file (`OAUTH2_PROXY_*`); terminate TLS in front of the proxy and set `--cookie-secure=true`.

### M4 — S3 remote backend has no state locking
`server/backend.tf` · `postgres/backend.tf` · `cache/backend.tf`
- **Issue:** The `backend "s3"` blocks have no lock mechanism (no DynamoDB, no `use_lockfile`). Concurrent applies (a local run + CI, or two operators) can interleave and **corrupt state**. The `-lock-timeout="$LOCK_TIMEOUT"` the module `deploy.sh` scripts pass has nothing to lock against — it's a no-op. This contradicts the modules' own header note ("back this with … locking").
- **Fix (revised 2026-08-21 — `use_lockfile` NOT viable):** A probe confirmed **vStorage does not enforce S3 conditional PUT (`If-None-Match`)** — a second `PutObject` with `IfNoneMatch:*` succeeded instead of returning `412`. So Terraform's S3-native locking (`use_lockfile`) would give **false safety**, and there is no DynamoDB. **Resolution: mitigate operationally** — CD already serialises via its `concurrency` group (single writer), and operators must never run two `apply`s against the same module/workspace at once. The `-lock-timeout` flags are harmless no-ops. (Revisit if vStorage gains conditional-PUT support.)

### M5 — Floating-IP discovery is inconsistent across scripts
App scripts (`deploy-api.sh:39`, `deploy-backend.sh:33`, `seed_data.sh:42`, `cache/deploy.sh:53`, `sso/deploy-sso.sh:56`, `proxy/deploy-caddy.sh:56`, `frontend/deploy-frontend.sh:46`, `ads-server/deploy-ads.sh:45`, `monitoring/deploy-monitoring.sh:85`) read `floating_ip` **only** from `internal_interfaces`; but `postgres/run-sql.sh:49-62` scans **both** `external_interfaces` and `internal_interfaces`.
- **Issue:** Contradictory assumptions about where the provider surfaces the public IP. If the schema ever places it under `external_interfaces` (which `run-sql.sh` anticipates), all app scripts silently fail with "no floating IP for server key …"; conversely `run-sql.sh` could target a different IP than the deploy scripts. (Provider schema not verifiable in this review.)
- **Fix:** Extract one shared helper that searches both interface lists (matching `run-sql.sh`) and use it everywhere.

### M6 — `load_balancer` has no remote backend but is an orchestrated IaC step
`load_balancer/` (no `backend.tf`) · `deploy-all.sh:174` · `lib/tfstate.sh:17`
- **Issue:** The LB module keeps **local** state, yet `deploy-all.sh` applies it via `tf_step` and `lib/tfstate.sh` aligns only `server postgres cache` to remote. From CI (fresh checkout) or a second machine there is no shared LB state → a re-apply creates a **duplicate load balancer** (or can't manage/destroy the existing one). (`storage/` also has local state, but that's the justified bootstrap chicken-and-egg — it creates the state bucket.)
- **Fix:** Add `backend "s3"` to `load_balancer` (`key = "load_balancer/terraform.tfstate"`) mirroring the other three, and add `load_balancer` to `TF_REMOTE_MODULES` in `lib/tfstate.sh:17`.

---

## Low

- **L1 — SSH host-key verification disabled everywhere** (`StrictHostKeyChecking=no`, `UserKnownHostsFile=/dev/null` in every `SSH_OPTS`, and `run-sql.sh:74`). MITM risk on connect — most impactful in `run-sql.sh`/`deploy-api.sh` where a spoofed bastion receives the DB password. Fix: pin host keys for stable hosts, or use `accept-new`.
- **L2 — `required_version` understates the real minimum.** `server/`, `postgres/`, `cache/`, `load_balancer/`, `storage/` `provider.tf` all say `>= 1.3`, but the S3 `backend.tf` needs **≥ 1.6** (`endpoints`, `use_path_style`, `skip_s3_checksum`). Running under 1.3–1.5 passes the gate then fails obscurely. Fix: set `required_version = ">= 1.6"` on the S3-backend modules.
- **L3 — GHCR credential persists on the VM.** `deploy-api.sh:167`, `deploy-backend.sh:95`, `frontend/deploy-frontend.sh:105`, `ads-server/deploy-ads.sh:137` `docker login ghcr.io` with no `logout`; token lingers in `~/.docker/config.json`. Fine for CI's ephemeral token, risky for a long-lived PAT. Fix: `docker logout ghcr.io` after pull, or use short-lived tokens. *(Introduced with the CD work.)*
- **L4 — Reading a sibling module's output mutates its selected workspace globally.** `terraform workspace select "$ENV"` writes `.terraform/environment`, so deploying `api` for `uat` silently switches `../postgres`/`../cache` to another workspace (footgun for a later manual `terraform` there). Not a script correctness bug. Fix: read outputs with an explicit workspace in one invocation, or restore the prior workspace.
- **L5 — `run-sql.sh` picks an arbitrary server as bastion** (`postgres/run-sql.sh:49-63`, `out[0]`). Non-deterministic which VM runs the SQL bootstrap. Fix: select by an explicit `*_SERVER_KEY` like the other scripts.
- **L6 — Bootstrap scripts continue after a failed client create.** `sso/bootstrap-realm.py:97-98`, `monitoring/bootstrap-oauth2-client.py:81-83`: `cuid` derived from `Location` with no status check → on failure `cuid=""` → malformed `.../clients//...` calls fail silently while the script prints "DONE". Fix: `sys.exit` when create status isn't 201/204 or `cuid` is empty.
- **L7 — `cache` prod apply skips the plan/review guard.** `cache/deploy.sh:124` runs `terraform apply -auto-approve` directly, whereas `postgres`/`server`/`load_balancer`/`storage` plan with `-detailed-exitcode -out=tfplan` then apply the saved plan. Fix: mirror the plan-then-apply-saved-plan pattern for cache prod.

---

## Nits

- **N1 — `storage/undeploy.sh:71`:** `${FORCE:+ (force)}` prints "(force)" even when `FORCE=0` (non-empty string). The actual `-var=force_destroy` gate at `:58` uses `-eq 1` and is correct — only the log label is wrong. Use `$([[ $FORCE -eq 1 ]] && echo ' (force)')`.
- **N2 — `server/deploy-api.sh:68` vs `:142`:** prod `REDIS_PORT` defaults to `6379` locally but the remote heredoc defaults to `6580` — the remote default is dead/inconsistent.
- **N3 — `server/deploy-api.sh:67`, `ads-server/deploy-ads.sh:71`:** hardcode `terraform workspace select prod` instead of `"$ENV"`. Correct only because `prod` is the sole non-`uat` env today; breaks if another is added.
- **N4 — `set-domain.sh:23`:** `--dry-run` is recognised only as the *first* argument; `./set-domain.sh newdomain uat --dry-run` would write for real. Parse the flag in any position.

---

## Recommended priority

1. **H1** — remove the wildcard redirect default (security, quick).
2. **M6** + **M4** — `load_balancer` remote backend + `use_lockfile = true` on all four (state integrity; directly hardens the CD work landed this session).
3. **M2 / M3** — move secrets off argv / harden oauth2-proxy (defense-in-depth).
4. **L2, L3, N1–N4** — quick correctness/consistency wins.
5. **M5, L4–L7** — consistency refactors (shared IP helper, explicit workspaces/server keys, plan guards).

*Findings are advisory; none block current operation — the pipeline is deploying uat successfully. Items tagged "introduced with the CD work" (M2 GHCR-token argv, L3) came from this session's changes; the rest predate it.*

---

## Remediation applied (2026-08-21)

A focused hardening pass fixed the top items:

- **H1 — FIXED.** `bootstrap-realm.py` now defaults `REDIRECT_URIS` to empty and **exits** if the list is empty or contains `*`; `deploy-all.sh` realm step drops the `:-*` fallback and `die`s unless `sso_redirect_uris` is set to an explicit, non-wildcard list. No more wildcard redirect by default.
- **M6 — FIXED.** Added `load_balancer/backend.tf` (S3/vStorage remote backend, `key=load_balancer/terraform.tfstate`), bumped its `required_version` to `>= 1.6`, added `load_balancer` to `TF_REMOTE_MODULES` in `lib/tfstate.sh`, and **migrated its existing local state to the remote bucket** (uat workspace + outputs verified). CI/second-machine can no longer spawn a duplicate LB.
- **M4 — RESOLVED as won't-fix-with-mitigation.** `use_lockfile` was investigated and **rejected**: vStorage does not enforce `If-None-Match` (verified probe), so native locking is not possible. Mitigation is operational (CD `concurrency` serialisation + no concurrent applies), now documented above and in the backend files.

Remaining M/L/Nit items are unaddressed and tracked in this report for a future pass.
