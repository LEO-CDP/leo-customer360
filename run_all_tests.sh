
# !/bin/bash

# One script to run all unit tests for the Leo Customer360 project, including backend jobs and API tests.
# This script is intended to be run from the root of the repository by developers and CI/CD pipelines.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

OVERALL_STATUS=0

run_suite() {
  local name="$1"
  local runner="$2"
  shift 2

  echo ""
  echo "==================================================================="
  echo "Running: ${name}"
  echo "==================================================================="

  if [ -x "$runner" ]; then
    "$runner" "$@" || OVERALL_STATUS=$?
  else
    echo "Runner not found or not executable: ${runner}"
    OVERALL_STATUS=1
  fi

  echo ""
  echo "-------------------------------------------------------------------"
  echo "Finished: ${name} (exit status so far: ${OVERALL_STATUS})"
  echo "-------------------------------------------------------------------"
}

run_suite "Customer 360 API" "${SCRIPT_DIR}/customer360-api/run_unit_tests.sh" "$@"
run_suite "Identity Resolution" "${SCRIPT_DIR}/backend-system/identity_resolution/run_tests.sh" "$@"

echo ""
echo "==================================================================="
if [ "$OVERALL_STATUS" -eq 0 ]; then
  echo "All unit test suites passed."
else
  echo "One or more unit test suites failed (overall exit status: ${OVERALL_STATUS})."
fi
echo "==================================================================="

exit "$OVERALL_STATUS"
