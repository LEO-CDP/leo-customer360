# shellcheck shell=bash
# otel.sh — shared OpenTelemetry (OTLP -> Jaeger) env wiring for the deploy scripts.
#
# The three FastAPI images (customer360-api, ads-server, frontend-admin) are
# instrumented with OpenTelemetry ZERO-CODE auto-instrumentation (see each
# service Dockerfile: `opentelemetry-bootstrap -a install` +
# `opentelemetry-instrument uvicorn ...`). This helper emits the OTEL_* lines
# to append to a service's container env-file so traces are exported over OTLP
# to the Jaeger all-in-one deployed by deployments/monitoring.
#
# Policy (matches the agreed design):
#   * UAT  -> tracing OFF by default. The api box is a tiny 1 vCPU / 2 GB host
#             shared by every service; we pay ZERO overhead until profiling.
#             OTEL_SDK_DISABLED=true makes the instrumentation a no-op.
#             To profile on demand, see deployments/monitoring/README.md (Jaeger section).
#   * PROD -> tracing ON at 10% head sampling (dedicated boxes have headroom).
#
# Override per run with environment variables:
#   OTEL_ENABLED=true|false          force tracing on/off (SDK enabled/disabled)
#   OTEL_ENDPOINT=http://host:4318   OTLP/HTTP collector (Jaeger) base endpoint
#   OTEL_SAMPLER_ARG=0.1             head-sampling ratio when enabled (0.0–1.0)
#
# Usage:  otel_env_lines <service_name> <uat|prod> [jaeger_host]
#   - jaeger_host defaults to 127.0.0.1 (UAT: Jaeger is co-located on the same
#     --network host box). For PROD cross-box services, pass the monitoring
#     box's PRIVATE ip (the caller resolves it from ../server outputs).
#
# Prints the OTEL_* env lines to STDOUT; prints a one-line status to STDERR so
# it stays out of the captured env blob.
otel_env_lines() {
  local svc="$1" env="$2" jhost="${3:-127.0.0.1}"
  local enabled sampler endpoint disabled

  if [[ "$env" == "prod" ]]; then enabled="true"; sampler="0.1"; else enabled="false"; sampler="1.0"; fi
  enabled="${OTEL_ENABLED:-$enabled}"
  sampler="${OTEL_SAMPLER_ARG:-$sampler}"
  endpoint="${OTEL_ENDPOINT:-http://${jhost}:4318}"
  [[ "$enabled" == "true" ]] && disabled="false" || disabled="true"

  cat <<OTELF
OTEL_SERVICE_NAME=$svc
OTEL_TRACES_EXPORTER=otlp
OTEL_METRICS_EXPORTER=none
OTEL_LOGS_EXPORTER=none
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_EXPORTER_OTLP_ENDPOINT=$endpoint
OTEL_TRACES_SAMPLER=parentbased_traceidratio
OTEL_TRACES_SAMPLER_ARG=$sampler
OTEL_PYTHON_LOG_CORRELATION=true
OTEL_SDK_DISABLED=$disabled
OTELF

  printf '>> Tracing: enabled=%s  endpoint=%s  sampler=%s  (OTEL_SDK_DISABLED=%s)\n' \
    "$enabled" "$endpoint" "$sampler" "$disabled" 1>&2
}
