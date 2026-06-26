feat(S11): find & merge duplicates

## Summary

Native on-demand duplicate detection and atomic, FK-complete contact merge. Admins tune dedup rules (weights, field selectors, threshold); the system uses pairwise difflib scoring to surface candidates; users confirm merges with a per-field preview. The merge engine is protected by a self-maintaining introspection test that fails the build if any contacts.id FK is missed from the reassignment manifest.

- **Dedup rule engine:** `dedupe_rule_set` table + admin CRUD; pairwise scoring via difflib (not S22's match_name)
- **Merge engine with safety net:** `_REASSIGNMENT_TARGETS` manifest (10 FK reassignments + 2 non-FK rewrites); atomic single-txn; collision-aware status/UNIQUE precedence
- **Introspection test guard:** walks `Base.metadata` for every contacts.id FK; fails build if missing from manifest
- **Frontend:** DuplicatesPage + MergeModal + DedupeRuleEditor; per-field chooser + survivor preview
- **Security:** PASS (no blockers); 3 hardening fixes applied

## Definition of Done

- [x] Migration `s11a1b2c3d4e5` (down_revision `s10a1b2c3d4e5`): new `dedupe_rule_set` table; seeded with Default set
- [x] Dedup rule engine: `dedupe_service.py` pairwise difflib scoring against active rule set
- [x] Merge engine with `_REASSIGNMENT_TARGETS` manifest: 10 FK reassignments + 2 non-FK rewrites; atomic; collision-aware
- [x] Introspection test: walks `Base.metadata`, fails build if any FK missing (self-maintaining guard)
- [x] Integration fixture: seeds survivor+loser in EVERY FK table; verifies zero residual loser refs + no UNIQUE violations
- [x] Endpoints: candidates/preview/merge/history (volunteer+) + rule-set CRUD (admin write); viewer 403
- [x] Frontend: DuplicatesPage + MergeModal + DedupeRuleEditor
- [x] Security audit PASS (no BLOCK); 3 hardening fixes (re-check is_deleted under lock, sanitize rollback detail, post-commit warnings)
- [x] Green gates: backend 32 isolated + segment 96 passed; frontend 434 tests + build + lint; security PASS
- [x] test_migrations.py EXPECTED_HEAD updated to `s11a1b2c3d4e5`

## Test Plan

- [x] Backend 32 isolated S11 tests + segment 96 (all affected files): **passed/0 failed**
- [x] Frontend 434 tests + build + lint: **green**
- [x] Security audit: **PASS** (no blockers; 3 hardening fixes applied)
- [x] Introspection test (`test_manifest_covers_all_contact_fks`): walks `Base.metadata`, verifies all contacts.id FKs in manifest
- [x] Integration fixture: survivor+loser in EVERY FK table (participants, detections, logs, group_members, name_alias, biometric_consent, etc.); post-merge verification
- [x] Recon findings: name_match_review_queue has both `contact_id` + `candidate_contact_id` (both reassigned); name_alias.alias_text is globally UNIQUE (collision handling corrected)

## Notable Implementation Details

1. **The headline safety net:** The `test_manifest_covers_all_contact_fks` introspection test walks `Base.metadata` for every contacts.id FK and FAILS the build if any is missing from `_REASSIGNMENT_TARGETS`. This is a self-maintaining guard — when S12 lands `activities.target_contact_id`, the test will break until that column is added to the manifest (intended behavior).

2. **Recon caught spec gaps:** 
   - name_match_review_queue has both `contact_id` and `candidate_contact_id` columns (not just `resolved_contact_id`); both are reassigned in the merge
   - name_alias.alias_text is globally UNIQUE (collision handling was missing from spec; corrected in merge logic with status-precedence rules)

3. **Security fixes (PASS, no blockers):**
   - Re-check is_deleted UNDER the with_for_update lock (closes TOCTOU double-merge → duplicate-audit-row window)
   - Sanitize rollback 500 detail string (no raw exception to client)
   - Sanitize post-commit warning strings

## Carry-Forwards

**🟡 Documented in docs/BLOCKERS.md:**

- **[S11] When S12 lands `activities.target_contact_id`,** the introspection test WILL FAIL until `("activities", "target_contact_id")` is added to `_REASSIGNMENT_TARGETS` (intended guard).
- **[S11] biometric_consent merge collision** deletes the loser's consent row (archived in audit before); "consent_given TRUE-wins" is a future option (one-liner).
- **[S11] /dedupe/merge + /dedupe/candidates** not endpoint-rate-limited (codebase-wide @limiter gap, same as S09/S10/S16).
- **[S11] Merge is irreversible-by-design;** the audit_log contact_merge row (full before/after) is the recovery/unmerge seed (no merged_into_id column, no unmerge UI this sprint).
- **[S11] is_default single-row invariant** is app-layer only (no DB partial unique index).

## Rollback Notes

Rollback is safe (Alembic downgrade):
```bash
cd backend && alembic downgrade s10a1b2c3d4e5
```

The migration is pure-additive (new `dedupe_rule_set` table); downgrade drops it. No data loss in contacts or other core tables.

## Migration Head

**New Alembic head:** `s11a1b2c3d4e5`  
**Down revision:** `s10a1b2c3d4e5`

Applied via:
```bash
cd backend && alembic upgrade head
```

---

Generated by senpai-v2-docs  
Green gate: backend ✅ + frontend ✅ + security ✅  
Date: 2026-06-26
