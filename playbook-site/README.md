# LEO Playbook site

End-user documentation for LEO CDP (use cases, features, roadmap), written in
Vietnamese Markdown under `docs/playbooks/` and built with
[Quartz](https://quartz.jzhao.xyz) into a static site. Target domain:
`playbook.leocdp.com`. The technical docs site (`docs-site/`) is separate and
excludes `docs/playbooks/`.

## Local build and preview

```bash
bash playbook-site/build.sh                      # -> .playbook-engine/public
bash playbook-site/build.sh --serve --port 8120  # build + live preview
```

The first run clones Quartz (pinned tag in `build.sh`) into `.playbook-engine/`
(git-ignored). Requires Node 22.

## Layout

| Path | Purpose |
|---|---|
| `docs/playbooks/` | The content (one Markdown page per topic) |
| `playbook-site/quartz.config.ts`, `quartz.layout.ts` | Site config and page layout |
| `playbook-site/custom.scss`, `quartz-components/` | Theme and custom components |
| `playbook-site/patches/` | Patches applied to Quartz at build time |
| `playbook-site/CONTENT-STANDARD.md` | Writing rules every page must follow |

## Patches

Applied with `git apply` on every build; if Quartz is upgraded and one no longer
applies, the build stops.

1. `0001-toc-label` - renames the table of contents to "Trên trang này".
2. `0002-explorer-scroll` - stops the sidebar explorer from scrolling the whole page.
3. `0003-vietnamese-search` - makes search work with Vietnamese diacritics.
4. `0004-breakpoints` - mobile up to 799px, tablet 800-1199px, desktop from 1200px.

## Deploy

Workflow: `.github/workflows/deploy-playbook.yml`. The site is static files only
(no backend). It builds on every branch but deploys nothing until step 4 is done.
Do the steps in this order; each one has a check.

1. **Bucket.** Nothing to do by hand: the deploy job creates `leo-playbook-uat` if it is
   missing and applies a public-read policy (`s3:GetObject` only, no listing) on every
   deploy. Terraform route (optional, then `import` the bucket if the job already made it):
   needs `deployments/storage/.env` (or `terraform.tfvars`) with the vStorage keys;
   `cd deployments/storage && ./deploy.sh uat plan`, then `apply`.
   Check after step 5: `curl -I https://hcm04.vstorage.vngcloud.vn/leo-playbook-uat/index.html`
   returns 200 (403 means the policy was not applied; read the deploy step log).
2. **DNS.** Create an A record `playbook.leocdp.com` -> the same LB IP as
   `beta.leocdp.com`. Check: `nslookup playbook.leocdp.com`.
3. **Caddy.** Actions > Deploy Caddy (UAT) > Run workflow: `validate` first, then `deploy`
   (same as `deploy-caddy.sh uat validate|deploy`, which needs the deploy SSH key). Its `playbook.leocdp.com` block serves HTTPS and maps
   folder URLs to `index.html`, which vStorage does not do itself. Check:
   `curl -I https://playbook.leocdp.com/` gets a valid cert and a 404 from the
   empty bucket (not a 502 or NoSuchBucket).
4. **Enable the deploy.** Set the Actions variable
   `PLAYBOOK_S3_BUCKET=leo-playbook-uat` (keep this exact name: the Caddyfile
   hardcodes it). Optional: `PLAYBOOK_S3_ENDPOINT` (default
   `https://hcm04.vstorage.vngcloud.vn`) and `PLAYBOOK_S3_REGION` (default
   `us-east-1`). The upload uses the existing `VSTORAGE_ACCESS_KEY` /
   `VSTORAGE_SECRET_KEY` secrets.
5. **Publish.** Actions > Playbook Site > Run workflow, on the default branch. After
   that, a merge that touches `docs/playbooks/**` or `playbook-site/**` publishes.
   Check: the "Deploy to vStorage" step is not skipped, and
   `curl -I https://playbook.leocdp.com/use-cases/ecommerce` returns 200 `text/html`.

Quartz links pages without `.html`, which S3 does not resolve, so the deploy job
also uploads each page under its extensionless key as `text/html`.

## Updating content

Whoever changes a product screen updates the matching `docs/playbooks/` page in
the same PR. Pages are reviewed against
[CONTENT-STANDARD.md](CONTENT-STANDARD.md) before merge.
