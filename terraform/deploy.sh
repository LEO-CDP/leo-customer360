#!/usr/bin/env bash
# Minimal single-click deploy for the Customer360 GreenNode Terraform stack.
# Usage: ./deploy.sh <dev|prod>   (or use deploy-dev.sh / deploy-prod.sh)
# Loads credentials from terraform/.env, then init + apply the chosen env.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV="${1:?usage: deploy.sh <dev|prod>}"

set -a; source "$DIR/.env"; set +a
cd "$DIR/environments/$ENV"
terraform init -input=false
terraform apply -auto-approve
