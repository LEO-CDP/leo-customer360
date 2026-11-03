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
(no backend). It builds on every branch but deploys nothing until all of this is
configured:

1. Create the bucket `leo-playbook-uat` (declared in
   `deployments/storage/overlays/uat.tfvars`): `cd deployments/storage &&
   ./deploy.sh uat plan`, check it only adds that bucket, then `./deploy.sh uat
   apply`. Make it publicly readable in the vStorage console.
2. Point DNS for `playbook.leocdp.com` at the same load balancer as
   `beta.leocdp.com`, then redeploy Caddy (`deployments/proxy/deploy-caddy.sh uat
   validate`, then `uat`). Its `playbook.leocdp.com` block serves HTTPS and maps
   folder URLs to `index.html`, which vStorage does not do itself.
3. In this repo's Actions variables, set `PLAYBOOK_S3_BUCKET=leo-playbook-uat`.
   Optional: `PLAYBOOK_S3_ENDPOINT` (default `https://hcm04.vstorage.vngcloud.vn`)
   and `PLAYBOOK_S3_REGION` (default `us-east-1`). The upload uses the existing
   `VSTORAGE_ACCESS_KEY` / `VSTORAGE_SECRET_KEY` secrets.

Then a merge to the default branch that touches `docs/playbooks/**` or
`playbook-site/**` publishes the site. To publish without a content change, run
the workflow by hand (Actions > Playbook Site > Run workflow) on the default branch.

Quartz links pages without `.html`, which S3 does not resolve, so the deploy job
also uploads each page under its extensionless key as `text/html`.

## Updating content

Whoever changes a product screen updates the matching `docs/playbooks/` page in
the same PR. Pages are reviewed against
[CONTENT-STANDARD.md](CONTENT-STANDARD.md) before merge.
