#!/usr/bin/env bash
###############################################################################
# Customer 360 / backend-system - Dagster local dev orchestrator
# Stops the `dagster dev` process (and its whole process group) started by
# start.sh.
###############################################################################
set -Eeuo pipefail

PROJECT_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$PROJECT_HOME/.dagster.pid"

GREEN="\033[0;32m"
YELLOW="\033[1;33m"
NC="\033[0m"

PID=""
if [ -f "$PID_FILE" ]; then
    PID="$(cat "$PID_FILE")"
fi

# Fallback: find it by command line if the PID file is missing/stale.
if [ -z "$PID" ] || ! kill -0 "$PID" 2>/dev/null; then
    PID="$(pgrep -f "dagster dev -w workspace.yaml" || true)"
fi

if [ -z "$PID" ]; then
    echo -e "${YELLOW}No running backend-system Dagster process found.${NC}"
    rm -f "$PID_FILE"
else
    # start.sh launches dagster via `setsid`, making it a process-group
    # leader -- signal the whole group (webserver + daemon + per-service
    # code servers), not just the top-level PID, since dagster does not
    # always cascade a plain SIGTERM to every child in time.
    echo "Stopping backend-system Dagster (PID $PID, process group)..."
    kill -TERM -- "-$PID" 2>/dev/null || kill "$PID" 2>/dev/null || true

    for _ in $(seq 1 15); do
        if ! kill -0 "$PID" 2>/dev/null; then
            break
        fi
        sleep 1
    done

    if kill -0 "$PID" 2>/dev/null; then
        echo -e "${YELLOW}Still running -- sending SIGKILL to the process group...${NC}"
        kill -KILL -- "-$PID" 2>/dev/null || kill -9 "$PID" 2>/dev/null || true
    fi
fi

# Safety net: killing the top-level PID/group can still leave orphans behind
# (observed in practice when SIGKILL hits the leader before it relays the
# signal). Sweep for any leftover dagster process whose command line
# references this checkout's backend-system directory (webserver/daemon
# reference it via DAGSTER_HOME, code servers via their dagster_defs.py
# path) -- scoped to PROJECT_HOME so this can't touch unrelated Dagster
# processes elsewhere on the machine, and matched against "dagster" too so
# it can never match this script's own `bash .../stop.sh` invocation.
LEFTOVER_PIDS=""
for candidate_pid in $(pgrep -f "dagster" || true); do
    if ps -p "$candidate_pid" -o args= 2>/dev/null | grep -qF "$PROJECT_HOME"; then
        LEFTOVER_PIDS="$LEFTOVER_PIDS $candidate_pid"
    fi
done

if [ -n "$LEFTOVER_PIDS" ]; then
    echo -e "${YELLOW}Cleaning up leftover Dagster process(es):${LEFTOVER_PIDS}${NC}"
    # shellcheck disable=SC2086
    kill -9 $LEFTOVER_PIDS 2>/dev/null || true
fi

rm -f "$PID_FILE"
echo -e "${GREEN}Stopped.${NC}"
