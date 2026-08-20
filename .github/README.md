# CI / CD — `leo-customer360`

This folder holds the GitHub Actions automation for the monorepo. The single
workflow ([`workflows/ci.yml`](workflows/ci.yml)) runs the test suite on every
change and then **builds only the service(s) that actually changed**, pushing
their Docker images to the GitHub Container Registry (GHCR) on `main`.

![CI/CD pipeline](ci-pipeline.png)

> Diagram source: [`ci-pipeline.excalidraw`](ci-pipeline.excalidraw) — open it at
> [excalidraw.com](https://excalidraw.com) to edit, then re-export `ci-pipeline.png`.

---

## How it works

### 1. Triggers

The workflow runs on:

- **`push`** to any branch
- **`pull_request`** targeting `main` / `master`

It is **skipped** when a change touches *only* `docs/**` or `ui-wireframes/**`
(nothing to test or build there).

### 2. `unit-tests`

Runs `run_all_tests.sh` (pytest) on Python 3.11, uploads the log as an
artifact, and emails a pass/fail report via Brevo SMTP. This is the existing
job — unchanged.

### 3. `changes` — detect what moved

Uses [`dorny/paths-filter`](https://github.com/dorny/paths-filter) to compare
the diff against a filter per service. Its `changes` output is a **JSON array of
the service names that changed** (e.g. `["ads-server","frontend-admin"]`), which
feeds the build matrix directly.

### 4. `build-and-push` — one image per changed service

A matrix job (`needs: [changes, unit-tests]`) that runs once **per changed
service**. If nothing relevant changed, it is skipped entirely.

- **Builds** the service's `Dockerfile` with Buildx + a per-service GHA layer cache.
- **Pushes to GHCR only on `main`** — tagged with the commit SHA and `latest`.
- On feature branches and PRs it **builds but does not push**, which still
  catches Dockerfile breakage before merge.

> Image push is gated on `unit-tests` passing: a red test run ships no images.

---

## Services & images

| Service          | Build context        | Port | Image on GHCR                                          |
| ---------------- | -------------------- | ---- | ----------------------------------------------------- |
| `ads-server`     | `./ads-server`       | 9009 | `ghcr.io/leo-cdp/leo-customer360/ads-server`          |
| `backend-system` | `./backend-system`   | 3000 | `ghcr.io/leo-cdp/leo-customer360/backend-system`      |
| `customer360-api`| `./customer360-api`  | 8008 | `ghcr.io/leo-cdp/leo-customer360/customer360-api`     |
| `frontend-admin` | `./frontend-admin`   | 8890 | `ghcr.io/leo-cdp/leo-customer360/frontend-admin`      |

Each image is published with two tags on `main`:

- `sha-<full-git-sha>` — immutable, use this for deploys and rollbacks
- `latest` — the newest build on the default branch

```bash
# Pull a specific, reproducible build
docker pull ghcr.io/leo-cdp/leo-customer360/customer360-api:sha-<git-sha>

# Or the latest from main
docker pull ghcr.io/leo-cdp/leo-customer360/customer360-api:latest
```

---

## Operating notes

- **Registry auth.** Push uses the built-in `GITHUB_TOKEN` with `packages: write`
  (scoped to the build job). No extra secret needed. Make sure the org allows
  Actions to write packages, and that each package's visibility/access is set as
  you want under the repo/org **Packages** settings.
- **Lowercase image names.** GHCR requires lowercase paths. `docker/metadata-action`
  lowercases automatically, so the uppercase org (`LEO-CDP`) in
  `${{ github.repository }}` is handled for you.
- **Add a service.** Add one line to the `changes` job's `filters:` and the
  matrix picks it up — no other change needed (the build step is generic:
  `context: ./<service>`, `file: ./<service>/Dockerfile`).
- **`backend-system/identity_resolution`** has its own `Dockerfile` but is not
  built here yet. Add a filter + a dedicated matrix leg if you want it published.
- **Want frontend images independent of Python tests?** Change
  `build-and-push`'s `needs:` to drop `unit-tests` (trade-off: images could then
  publish even if tests fail).

---

## Secrets used

| Secret               | Used by       | Purpose                          |
| -------------------- | ------------- | -------------------------------- |
| `GITHUB_TOKEN`       | build-and-push| GHCR login/push (auto-provided)  |
| `BREVO_SMTP_LOGIN`   | unit-tests    | Email report auth                |
| `BREVO_SMTP_KEY`     | unit-tests    | Email report auth                |
| `BREVO_SENDER_EMAIL` | unit-tests    | Email report `from:` address     |
