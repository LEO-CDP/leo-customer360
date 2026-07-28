# Segments API Improvement Plan FOR TABLE cdp_segments

## 1) Why Segments APIs are missing currently

Most of the Segments API surface **already exists** (`core/routers/segment.py`), so this is a gap-filling plan, not a from-scratch build:

- **Existing today**: full CRUD (`/api/v1/segments/`) via the generic router factory, plus two read-only endpoints that execute a segment's `sql_rules` live against `cdp_master_profiles` (`GET /{id}/matched-profiles`, `GET /{id}/matched-profiles/count`), plus an admin `POST /segments/admin/defaults/seed` to seed system-default segments per tenant. Rule-fragment safety is enforced both at write time (`core/schemas/segmentation.py` validators) and again immediately before every execution (`core/utils/sql_safety.py`), defending against rows written outside the API (e.g. `core/init_core_data.py`'s direct ORM inserts).

- **What's missing**: `cdp_segments` has `member_count` and `last_computed_at` columns, but **nothing ever populates them** — `matched-profiles`/`matched-profiles/count` run a live query but never write the result back onto the segment row. There is also no mechanism that writes the segment's `segment_tag` into `cdp_master_profiles.segmentation_tags` for matching profiles, even though `core/routers/content.py` already reads `segmentation_tags` to drive content recommendations — i.e., the consumer of segment membership exists, but nothing produces the data it depends on.

- **No dry-run rule validation**: Admins/AI agents building a segment in the jQuery QueryBuilder UI have no way to preview match count/sample profiles for a rule tree *before* saving it as a segment row — `sql_rules` must already be persisted to test it.

- **No batch/scheduled recompute**: Unlike identity resolution (`backend-system/identity_resolution`, Dagster job + sensor), segment recomputation has no equivalent background job — it's entirely on-demand/live-query today.

## 2) Target behavior 

1. **Recompute endpoint** — `POST /api/v1/segments/{segment_id}/recompute`:
   - Re-runs the segment's `sql_rules` against `cdp_master_profiles` for its tenant.
   - Updates `member_count` and `last_computed_at` on the segment row.
   - Appends/keeps `segment_tag` in `segmentation_tags` for every currently-matching profile, and removes it from profiles that no longer match (idempotent tag sync, not append-only).

2. **Dry-run validation** — `POST /api/v1/segments/dry-run`:
   - Accepts an unsaved `sql_rules` fragment (+ `tenant_id`, `domain`), validates it with the same `validate_sql_where_fragment` safety net, executes it read-only, and returns `{ "matched_count": N, "sample_profiles": [...] }` without persisting anything.

3. **Scheduled recompute (Dagster)** — a `backend-system/segmentation/` job (already a placeholder per `backend-system/README.md`) periodically calls the same recompute logic for all `is_active = true` segments across tenants, keeping `member_count`/tags fresh without an explicit admin action.

4. **Existing CRUD/matched-profiles/seed-defaults endpoints stay unchanged** — this plan only adds the recompute + dry-run layer on top.

## 3) Gaps between current and target

| Gap | Current State | Target State | Priority |
|-----|---------------|--------------|----------|
| **member_count / last_computed_at never populated** | Columns exist, default to 0/NULL, never updated | `POST /segments/{id}/recompute` updates both after running `sql_rules` | High |
| **No tag write-back to master profiles** | `segmentation_tags` read by `content.py` but nothing writes segment membership into it | Recompute syncs `segment_tag` into/out of `cdp_master_profiles.segmentation_tags` for matching/non-matching profiles | High |
| **No dry-run/preview of unsaved rules** | Rules must be saved as a segment row before they can be tested | `POST /segments/dry-run` validates + executes an ad-hoc fragment, returns count + sample, no persistence | Medium |
| **No scheduled recompute** | Fully on-demand; segments go stale if no admin recomputes them | `backend-system/segmentation` Dagster job periodically recomputes all active segments | Medium |
| **No tests for recompute/dry-run** | `tests/test_segment_router.py` covers CRUD + matched-profiles + seed-defaults only | Add unit tests for recompute (count/tag updates) and dry-run (validation + execution) | High |

## 4) Implementation (recommended sequence)

### Phase 1: Recompute endpoint (2–3 hours)
1. Add `recompute_segment_membership(db, segment)` to `core/crud/` (new `core/crud/segmentation.py` or alongside `_segment_crud` in `segment.py`):
   - Re-validates `sql_rules` via `validate_sql_where_fragment` (same helper already used in `_validated_where_fragment`).
   - Runs a tenant-scoped `SELECT master_profile_id FROM cdp_master_profiles WHERE tenant_id = :tenant_id AND status_code = 1 AND (<where_fragment>)`.
   - In one transaction: `UPDATE cdp_master_profiles SET segmentation_tags = array_append(...)` for newly-matching rows lacking the tag, and `array_remove(...)` for rows that have the tag but no longer match.
   - Updates the segment's `member_count` (matched row count) and `last_computed_at = now()`.
2. Add route in `core/routers/segment.py`:
   ```python
   @segments_router.post("/{segment_id}/recompute")
   def recompute_segment(segment_id: uuid.UUID, db: Session = Depends(get_db)):
       segment = _get_segment_or_404(db, segment_id)
       if not segment.sql_rules:
           raise HTTPException(status_code=400, detail="Segment has no sql_rules to compute")
       result = recompute_segment_membership(db, segment)
       return {"segment_id": str(segment_id), "member_count": result.member_count, "last_computed_at": result.last_computed_at}
   ```
3. Invalidate the `segments/matched_profiles*` cache prefix on recompute (reuse the existing `core/cache.py` invalidation pattern used elsewhere on writes).

### Phase 2: Dry-run endpoint (1–2 hours)
1. Add `DryRunRequest`/`DryRunResult` schemas to `core/schemas/segmentation.py` (`tenant_id`, `domain`, `sql_rules`, optional `limit` for sample size).
2. Add `POST /segments/dry-run` route reusing `_validated_where_fragment` + a read-only query identical in shape to `get_segment_matched_profiles`, but against the request body instead of a stored segment row. No DB write.

### Phase 3: Scheduled recompute job (2–3 hours)
1. Replace the placeholder single-op job in `backend-system/segmentation/dagster_defs.py` with an op that queries all `is_active = true` segments across tenants and calls the same `recompute_segment_membership` logic (import shared code or duplicate the SQL, matching the existing `identity_resolution` Dagster job style).
2. Optionally add a schedule (e.g. every 6 hours) analogous to `identity_resolution`'s sensor, documented in `backend-system/README.md`.

### Phase 4: Tests (1–2 hours)
1. `tests/test_segment_router.py`: add cases for `recompute` (count updates, tag sync, 400 when no `sql_rules`, 404 for missing segment) and `dry-run` (valid fragment returns count+sample, unsafe fragment rejected with 400).
2. `backend-system/segmentation/` (or wherever Dagster tests live, mirroring `identity_resolution/tests/test_dagster_defs.py`): smoke test that the job executes without error against sample data.

## 5) Suggested API request shapes

### Recompute a segment
```
POST /api/v1/segments/<segment-uuid>/recompute
Response: {
  "segment_id": "<uuid>",
  "member_count": 1432,
  "last_computed_at": "2026-07-28T10:15:00Z"
}
```

### Dry-run an unsaved rule fragment
```
POST /api/v1/segments/dry-run
Body: {
  "tenant_id": "<uuid>",
  "domain": "retail",
  "sql_rules": "age >= 18 AND churn_risk_tier = 'low'",
  "limit": 10
}
Response: {
  "matched_count": 812,
  "sample_profiles": [
    { "master_profile_id": "<uuid>", "full_name": "...", "email": "..." }
  ]
}
```

## 6) Rollout strategy

**Phase 1 (Week 1):** Ship `recompute` endpoint behind existing auth; test manually against staging with a few known segments; verify `segmentation_tags` sync doesn't clobber unrelated tags (only add/remove this segment's own `segment_tag`).

**Phase 2 (Week 1–2):** Ship `dry-run` endpoint; have admin UI's QueryBuilder call it before "Save segment" to show a live preview count.

**Phase 3 (Week 2–3):** Enable the scheduled Dagster recompute job in staging first (short interval), confirm `member_count`/tags stay in sync with manual recompute results, then promote to production with a longer interval (e.g. every 6 hours).

**Phase 4:** Roll out incrementally per tenant if needed; monitor query cost of recompute (the `sql_rules` WHERE fragment runs against full `cdp_master_profiles` per segment — watch for slow segments and consider adding indexes on commonly-filtered columns).

## 7) Definition of done

- [ ] `recompute_segment_membership` logic implemented, updates `member_count`, `last_computed_at`, and syncs `segment_tag` into/out of `cdp_master_profiles.segmentation_tags`
- [ ] `POST /api/v1/segments/{segment_id}/recompute` endpoint wired, 400 when no `sql_rules`, 404 for missing segment
- [ ] `POST /api/v1/segments/dry-run` endpoint wired, validates fragment, executes read-only, no persistence
- [ ] Cache invalidation on recompute for `segments/matched_profiles*` prefixes
- [ ] `backend-system/segmentation/dagster_defs.py` replaced with real recompute-all-active-segments job
- [ ] Unit tests added for recompute (count/tag sync/error cases) and dry-run (valid/invalid fragment)
- [ ] `docs/customer360-api.md` updated with `/segments/{id}/recompute` and `/segments/dry-run` sections
- [ ] Code review + team sign-off
- [ ] Validated in staging (manual recompute + scheduled job) before production rollout
