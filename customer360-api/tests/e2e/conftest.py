"""Shared config + fixtures for the UAT end-to-end suite (SCRUM-93 / SCRUM-94).

Drives a LIVE deployment over HTTP. Everything is env-driven (E2E_*), and every
test skips when E2E_BASE_URL is unset, so the suite is inert in the hermetic unit
run. See tests/e2e/README.md and tests/e2e/TEST_PLAN.md.

Auth (core/auth.py): SSO on -> Authorization: Bearer <token>; SSO off ->
X-Tenant-Id / X-User-Id headers.

Data safety: a per-test ``track`` fixture DELETEs every resource it is handed
(LIFO, tolerant) in teardown -- even on failure -- so nothing lingers on the
shared UAT tenant. Deleting a segment cascades its crm_segment_sync_runs rows.
"""

import base64
import hashlib
import hmac
import os
import uuid

import httpx
import pytest

BASE_URL = os.environ.get("E2E_BASE_URL", "").rstrip("/")
API_PREFIX = os.environ.get("E2E_API_PREFIX", "/api/v1")
BEARER_TOKEN = os.environ.get("E2E_BEARER_TOKEN", "").strip()
TENANT_ID = os.environ.get("E2E_TENANT_ID", "").strip()
USER_ID = os.environ.get("E2E_USER_ID", "").strip()
SEGMENT_ID = os.environ.get("E2E_SEGMENT_ID", "").strip()
# Impossible predicate by default: zero matches -> a real sync writes NO crm_*
# rows (safe on shared UAT). Routing tests build their own narrow predicates.
SEGMENT_SQL = os.environ.get("E2E_SEGMENT_SQL", "engagement_score < -1")
VERIFY_TLS = os.environ.get("E2E_VERIFY_TLS", "true").strip().lower() not in ("false", "0", "no")
TIMEOUT = float(os.environ.get("E2E_TIMEOUT", "60"))
# Opt-in gate for tests that write to crm_* target tables (Route A/B/C).
ALLOW_DATA_WRITES = os.environ.get("E2E_ALLOW_DATA_WRITES", "").strip().lower() in ("1", "true", "yes")

# HMAC secret for minting email tracking tokens; must match the deployment's
# EMAIL_TRACKING_SECRET (UAT leaves it at this default).
EMAIL_TRACKING_SECRET = os.environ.get("E2E_EMAIL_TRACKING_SECRET", "leocdp-dev-tracking-secret")

# DELETE endpoints for each resource kind the suite creates (relative to API_PREFIX).
DELETE_PATHS = {
    "segment": "/segments/{}",
    "campaign": "/campaigns/{}",
    "content-item": "/content-items/{}",
    "lead": "/leads/{}",
    "lead-source": "/lead-sources/{}",
    "contact": "/contacts/{}",
    "customer-contact": "/customer-contacts/{}",
    "transaction": "/transactions/{}",
}


# ---------------------------------------------------------------------------
# TEST_PLAN.md case selection.
#   pytest tests/e2e --case S94-02 --case R-A1   # specific cases
#   pytest tests/e2e --case S94                  # prefix -> all S94-* cases
# A test is tagged with @pytest.mark.case("S94-02", ...) for the plan id(s) it
# covers. No --case given -> run everything.
# ---------------------------------------------------------------------------
def pytest_addoption(parser):
    parser.addoption(
        "--case", action="append", default=[], metavar="ID",
        help="Run only tests whose TEST_PLAN.md case id matches (prefix ok): --case S94-02 --case R",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "case(*ids): TEST_PLAN.md case id(s) this test covers")


# nodeid -> [TEST_PLAN case ids], populated at collection for the run summary.
_CASE_BY_NODEID: dict = {}


def pytest_collection_modifyitems(config, items):
    for item in items:
        _CASE_BY_NODEID[item.nodeid] = [str(a) for m in item.iter_markers(name="case") for a in m.args]
    requested = [r.strip() for r in config.getoption("--case") if r.strip()]
    if not requested:
        return
    kept, deselected = [], []
    for item in items:
        ids = _CASE_BY_NODEID.get(item.nodeid, [])
        if any(cid == tok or cid.startswith(tok) for cid in ids for tok in requested):
            kept.append(item)
        else:
            deselected.append(item)
    if deselected:
        config.hook.pytest_deselected(items=deselected)
        items[:] = kept


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Write a Markdown run summary (case id, test, result, time) to
    GITHUB_STEP_SUMMARY (CI) or E2E_SUMMARY_MD (local) so E2E results are visible
    on the GitHub Actions run's Summary tab. No-op when neither is set."""
    path = os.environ.get("GITHUB_STEP_SUMMARY") or os.environ.get("E2E_SUMMARY_MD")
    if not path:
        return
    rank = {"passed": 0, "skipped": 1, "error": 2, "failed": 3}
    emoji = {"passed": "✅", "skipped": "⏭️", "error": "💥", "failed": "❌"}

    def _skip_reason(rep) -> str:
        # A skip report's longrepr is (file, lineno, "Skipped: <reason>").
        lr = getattr(rep, "longrepr", None)
        text = str(lr[2]) if isinstance(lr, tuple) and len(lr) == 3 else (str(lr) if lr else "")
        return text.replace("Skipped: ", "", 1).strip()

    outcome, duration, reason = {}, {}, {}
    for status in ("passed", "failed", "error", "skipped"):
        for rep in terminalreporter.stats.get(status, []):
            nodeid = getattr(rep, "nodeid", None)
            if not nodeid:
                continue
            if nodeid not in outcome or rank[status] >= rank[outcome[nodeid]]:
                outcome[nodeid] = status
            duration[nodeid] = duration.get(nodeid, 0.0) + (getattr(rep, "duration", 0.0) or 0.0)
            if status == "skipped":
                reason[nodeid] = _skip_reason(rep)

    counts = {"passed": 0, "failed": 0, "error": 0, "skipped": 0}
    for st in outcome.values():
        counts[st] += 1

    def _detail(nid) -> str:
        return reason.get(nid, "").replace("|", "\\|").replace("\n", " ").strip()

    rows = sorted(
        ((", ".join(_CASE_BY_NODEID.get(nid, [])) or "-", nid.split("::", 1)[-1], st,
          duration.get(nid, 0.0), _detail(nid))
         for nid, st in outcome.items()),
        key=lambda r: (r[0], r[1]),
    )
    overall = "❌ FAILED" if (counts["failed"] or counts["error"]) else ("✅ PASSED" if outcome else "⚠️ NO TESTS")
    lines = [
        "## E2E — SCRUM-93 / SCRUM-94 (customer360-api → UAT)",
        "",
        f"**{overall}** — {counts['passed']} passed · {counts['skipped']} skipped · "
        f"{counts['failed']} failed · {counts['error']} error  ·  target `{BASE_URL or '(unset)'}`",
        "",
        "| Case | Test | Result | Time | Reason (why skipped) |",
        "| --- | --- | --- | --- | --- |",
        *[f"| {cases} | `{name}` | {emoji[st]} {st} | {dur:.2f}s | {detail or '—'} |"
          for cases, name, st, dur, detail in rows],
        "",
    ]
    if counts["skipped"]:
        lines += [
            "> **Skips are intentional, env-gated** (not failures): routing/write tests "
            "(`test_routing_e2e.py`, Route A/B/C) need `E2E_ALLOW_DATA_WRITES=1` because they "
            "write to `crm_*` tables on the shared UAT tenant; the cross-tenant isolation test "
            "needs `E2E_TENANT_ID_B` + `E2E_BEARER_TOKEN_B`. See `tests/e2e/TEST_PLAN.md`.",
            "",
        ]
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except Exception:
        pass


def _auth_headers() -> dict:
    headers = {}
    if BEARER_TOKEN:
        headers["Authorization"] = f"Bearer {BEARER_TOKEN}"
    if TENANT_ID:
        headers["X-Tenant-Id"] = TENANT_ID
    if USER_ID:
        headers["X-User-Id"] = USER_ID
    return headers


@pytest.fixture(scope="session")
def p():
    """Prefix an API path: ``p('/segments/') -> '/api/v1/segments/'``."""
    return lambda path: f"{API_PREFIX}{path}"


@pytest.fixture(scope="session")
def client():
    if not BASE_URL:
        pytest.skip("E2E_BASE_URL not set")
    with httpx.Client(
        base_url=BASE_URL, headers=_auth_headers(), timeout=TIMEOUT,
        follow_redirects=True, verify=VERIFY_TLS,
    ) as c:
        yield c


@pytest.fixture(scope="session")
def unauth_client():
    if not BASE_URL:
        pytest.skip("E2E_BASE_URL not set")
    with httpx.Client(base_url=BASE_URL, timeout=TIMEOUT, follow_redirects=True, verify=VERIFY_TLS) as c:
        yield c


@pytest.fixture(scope="session")
def tenant_id():
    if not TENANT_ID:
        pytest.skip("E2E_TENANT_ID not set (required to build request bodies)")
    return TENANT_ID


class _Tracker:
    """Records (kind, id) created during a test and DELETEs them in teardown."""

    def __init__(self, client):
        self._client = client
        self._items: list[tuple[str, str, bool]] = []

    def add(self, kind: str, resource_id, neutralize: bool = False) -> str:
        """Register a resource for deletion. ``neutralize=True`` (segments only):
        before delete, repoint the segment to a zero-match rule and re-sync so the
        sync engine's synchronous recompute strips the ephemeral segment_tag from
        any real profile it matched (delete alone does not untag)."""
        assert kind in DELETE_PATHS, f"unknown resource kind: {kind}"
        rid = str(resource_id)
        self._items.append((kind, rid, neutralize))
        return rid

    def cleanup(self) -> None:
        while self._items:
            kind, rid, neutralize = self._items.pop()  # LIFO: children before parents
            try:
                if kind == "segment" and neutralize:
                    self._client.patch(f"{API_PREFIX}/segments/{rid}", json={"sql_rules": "engagement_score < -1"})
                    self._client.post(f"{API_PREFIX}/admin/crm/sync-segment/{rid}")  # sync -> sync-recompute -> untag
                self._client.delete(f"{API_PREFIX}{DELETE_PATHS[kind].format(rid)}")
            except Exception:  # teardown must never mask the test result
                pass


@pytest.fixture
def track(client):
    """Per-test cleanup registrar. ``track.add('segment', sid)`` -> deleted after."""
    tracker = _Tracker(client)
    yield tracker
    tracker.cleanup()


@pytest.fixture
def make_segment(client, tenant_id, track):
    """Factory: create a tracked segment. Returns (segment_id, response_body)."""
    def _make(sql_rules=None, neutralize=False, **overrides):
        tag = f"e2e-{uuid.uuid4().hex[:8]}"
        body = {
            "tenant_id": tenant_id,
            "domain": "all",
            "segment_tag": tag,
            "segment_name": f"E2E {tag}",
            "description": "Ephemeral segment (e2e).",
            "processed_by": "human",
            "is_active": True,
            **overrides,
        }
        if sql_rules is not None:
            body["sql_rules"] = sql_rules
        resp = client.post(f"{API_PREFIX}/segments/", json=body)
        assert resp.status_code in (200, 201), f"segment create failed: {resp.status_code} {resp.text}"
        data = resp.json()
        track.add("segment", data["segment_id"], neutralize=neutralize)
        return data["segment_id"], data
    return _make


@pytest.fixture(scope="session")
def segment_id(client, tenant_id, p):
    """Shared zero-match segment for the default (no-write) suite. Reuses
    E2E_SEGMENT_ID if given; else creates an ephemeral one and deletes it on
    teardown (FK cascade drops its crm_segment_sync_runs rows)."""
    if SEGMENT_ID:
        yield SEGMENT_ID
        return
    tag = f"e2e-crm-sync-{uuid.uuid4().hex[:8]}"
    payload = {
        "tenant_id": tenant_id, "domain": "all", "segment_tag": tag,
        "segment_name": f"E2E CRM Sync {tag}", "description": "Ephemeral segment (e2e).",
        "sql_rules": SEGMENT_SQL, "processed_by": "human", "is_active": True,
    }
    resp = client.post(p("/segments/"), json=payload)
    assert resp.status_code in (200, 201), f"segment create failed: {resp.status_code} {resp.text}"
    sid = resp.json()["segment_id"]
    yield sid
    client.delete(p(f"/segments/{sid}"))


@pytest.fixture(scope="session")
def email_feature(unauth_client, p):
    """Skip SCRUM-97/98 tests when the target doesn't serve the email
    execution/tracking endpoints yet (e.g. UAT still on a pre-subtask-05/06
    build) -- the public open-pixel returns 200 only when they're deployed."""
    r = unauth_client.get(p("/track/email/open"))
    if r.status_code != 200:
        pytest.skip("email tracking/execution endpoints not deployed on target (SCRUM-97/98)")


@pytest.fixture(scope="session")
def mint_token():
    """Build a signed email tracking token (tenant|campaign|profile), matching
    backend-system email_engine's format, keyed by EMAIL_TRACKING_SECRET."""
    def _mint(tenant_id, campaign_id, master_profile_id, secret=EMAIL_TRACKING_SECRET):
        raw = f"{tenant_id}|{campaign_id}|{master_profile_id}"
        sig = hmac.new(secret.encode(), raw.encode(), hashlib.sha256).hexdigest()[:20]
        return base64.urlsafe_b64encode(f"{raw}|{sig}".encode()).decode().rstrip("=")
    return _mint


@pytest.fixture(scope="session")
def sign_click_url():
    """Build the click-URL signature (``k=``) the click endpoint verifies."""
    def _sign(url, secret=EMAIL_TRACKING_SECRET):
        return hmac.new(secret.encode(), url.encode(), hashlib.sha256).hexdigest()[:20]
    return _sign
