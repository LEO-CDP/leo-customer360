# shellcheck shell=bash
# tfvars.sh — shared readers for Terraform inputs/outputs used by the deploy scripts.
#
# Both helpers were previously copy-pasted verbatim into every deploy-*.sh (and
# seed_data.sh); they live here so a fix lands once. Source this from a module
# deploy script:  . "$(cd "$(dirname "$0")/.." && pwd)/lib/tfvars.sh"

# tfval <key> <tfvars-file>
#   Echo the value of `key = ...` from a .tfvars file: the content between the
#   quotes for strings (so a '#' inside the value survives), or the bare token
#   with any trailing comment stripped for unquoted numbers/bools. Empty when the
#   key or the file is absent.
tfval() {
  local line; line="$(grep -E "^[[:space:]]*$1[[:space:]]*=" "$2" 2>/dev/null | head -1)"
  case "$line" in
    *\"*\"*) line="${line#*\"}"; printf '%s' "${line%%\"*}" ;;
    *) line="${line#*=}"; line="${line%%#*}"; printf '%s' "$(printf '%s' "$line" | tr -d '[:space:]')" ;;
  esac
}

# srv_ip <server-key> <field>
#   Echo the first non-empty <field> (fixed_ip | floating_ip) of the named server
#   from the `servers` Terraform output. Requires the CALLER to have already set
#   SERVERS_JSON, e.g.:
#     SERVERS_JSON="$(terraform output -json servers)"
srv_ip() { printf '%s' "$SERVERS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d.get(sys.argv[1]) or {}; print(next((i.get(sys.argv[2]) for i in (s.get("internal_interfaces") or []) if i.get(sys.argv[2])), ""))' "$1" "$2"; }
