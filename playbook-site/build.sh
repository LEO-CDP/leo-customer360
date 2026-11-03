#!/usr/bin/env bash
# Builds the LEO Playbook site from docs/playbooks/ into .playbook-engine/public.
# Usage: ./playbook-site/build.sh [--serve]   (extra args go to `quartz build`)
set -euo pipefail

QUARTZ_REF="v4.5.2" # keep in step with .github/workflows/deploy-docs.yml

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SITE="$ROOT/playbook-site"
ENGINE="$ROOT/.playbook-engine"

if [ ! -d "$ENGINE/.git" ]; then
  git clone --depth 1 -b "$QUARTZ_REF" https://github.com/jackyzha0/quartz.git "$ENGINE"
fi

cd "$ENGINE"
git checkout -- .
# --ignore-whitespace: a Windows clone may have CRLF files while the patches are LF.
git apply --ignore-whitespace "$SITE"/patches/*.patch

cp "$SITE/quartz.config.ts" "$SITE/quartz.layout.ts" .
cp "$SITE/custom.scss" quartz/styles/custom.scss
cp "$SITE"/quartz-components/*.tsx quartz/components/

rm -rf content public
cp -R "$ROOT/docs/playbooks" content

[ -d node_modules ] || npm ci --no-audit --no-fund
npx quartz build "$@"
