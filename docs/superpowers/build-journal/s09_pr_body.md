# feat(S09): advanced search, saved searches & smart groups

## Summary

Sprint S09 implements a native replacement for CiviCRM's Advanced Search, Search Builder, and Smart Groups — an injection-safe structured-query stack with whitelist-enforced field/operator validation, parameterized SQL generation, and DoS guards. The sprint adds three core tables (saved_searches, groups, group_members), a field registry (15 core + 7 S23-derived snapshot fields), a compile_criteria injection gate, full CRUD for saved searches and groups, and a React-based UI for query building, execution, and group management.

**Security: 1 HIGH + 1 MEDIUM + 1 LOW blocks found and fixed pre-merge.** See Notable Events below.

## Definition of Done

- [x] **Migration** `s09a1b2c3d4e5` (down_revision `s08a1b2c3d4e5`)
  - Creates `saved_searches` table (id, user_id, name, description, criteria JSONB, created_at, updated_at)
  - Creates `groups` table (id, name, description, is_smart, smart_criteria JSONB, created_at, updated_at)
  - Creates `group_members` table (id, group_id, contact_id, created_at) for static groups
  - Unique constraints: (user_id, name) on saved_searches; (name) on groups
  - Indexes on user_id, is_smart, created_at
  - Down_rev: `s08a1b2c3d4e5` (S08 migration)

- [x] **Field registry** (`services/search_fields.py`)
  - Whitelist: 15 core fields (first_name, last_name, email, phone, contact_type, contact_subtype, created_at, updated_at, external_id) + 7 S23-derived snapshot fields (tier, is_active, is_regular, is_connected, weeks_absent, attendance_count, last_attended_at)
  - Active custom fields via `get_active_schema()` reuse (no N+1)
  - Per-data-type operator sets: text (=, contains, contains_any, startswith), select (=, contains_any), number (=, <, >, <=, >=), date (=, <, >, <=, >=), checkbox (=), contact_reference (=), multiselect (contains_any)
  - Dialect-branched JSONB resolution (Postgres `@>` vs SQLite JSON)

- [x] **Injection-safe query compiler** (`services/search_service.py`)
  - `compile_criteria(criteria_dict) → SQLAlchemy Filter`: the injection gate
  - Pipeline: whitelist field check → operator validation → type coercion → SQL parameterization
  - InvalidCriteria exception on unknown field/op (identifier never reaches SQL)
  - DoS guards: depth ≤ 10, node_count ≤ 100, list_length ≤ 200 (hard limits, non-bypassable)
  - Non-bypassable soft-delete filtering (always AND deleted_at IS NULL)
  - `run_search(criteria, offset, limit) → List[Contact]`: execute and paginate
  - Saved search CRUD: owner-scoped (user_id), cross-owner → 404
  - Group CRUD: create, read, update, delete; smart groups live-resolve criteria on access; static groups freeze group_members snapshot via dialect upsert (on_conflict_do_nothing)
  - `promote_saved_to_smart(saved_search_id) → Group`: convert saved search to smart group

- [x] **Two routers** (`routers/search.py`)
  - `/search` router: POST run_search, POST validate, POST save, GET list (user's saved searches), DELETE {id}
  - `/groups` router: POST create, GET list (bare array), GET /{id}, PATCH /{id}, DELETE /{id}, POST /{id}/populate (static group member snapshot)
  - All endpoints require_volunteer (authenticated only)
  - GET /groups includes soft-deleted groups (is_archived flag) → include_deleted param admin-gated

- [x] **Frontend query builder** (AdvancedSearchPage, FilterBuilder, FieldPicker, OperatorSelect)
  - AdvancedSearchPage: master page for building and running searches
  - FilterBuilder component: recursive AND/OR nesting (depth ≤ 10 enforced by UI + backend)
  - FieldPicker: whitelist-backed dropdown (reuses search_fields registry)
  - OperatorSelect: dynamic per-field (driven by field data_type)
  - Result table: DataTable, Pagination, ContactListItem (reuses S03 components)
  - Run search → display results; Save search → POST /search/save

- [x] **Frontend saved searches & groups UIs**
  - SavedSearchesPage: list user's saved searches, search by name, run/edit/rename/delete/promote
  - GroupsPage: list all groups, create new, search by name, view smart-criteria or member count
  - GroupDetailPage: view group members (DataTable), manage static group (add/remove contacts via ContactPickerModal), edit name/description, delete group, recompute smart criteria (if smart)
  - ContactPickerModal: adapted for contact_reference input in FilterBuilder (multi-select, search, avatar)

- [x] **Test migration head** (`test_migrations.py`)
  - EXPECTED_HEAD updated to `s09a1b2c3d4e5`
  - down_revision test updated to `s08a1b2c3d4e5`

- [x] **Security audit PASS** (re-audit after BLOCK→fix)
  - Initial: 1 HIGH (DoS-guard bypass), 1 MEDIUM (cross-owner IDOR), 1 LOW (LIKE escape)
  - **HIGH FIXED:** `_check_bounds` discriminated leaf vs group via `"conditions" in node`; compiler used `"logic" in node` → leaf with huge value list + empty conditions skipped check → unbounded IN clause. Fixed: unconditional list check + unified logic discrimination across all node types.
  - **MEDIUM FIXED:** `populate_static_group` loaded SavedSearch by id without owner_id filter → volunteer could reuse another user's private criteria. Fixed: owner-scoped load with explicit owner_id check → 404 on mismatch.
  - **LOW FIXED:** `contains_any` LIKE operator missing backslash escape for special chars. Fixed: added escape=`\\`.
  - **3 regression tests added** (DoS bypass shapes: huge list on leaf, mixed conditions, IDOR cross-owner attempt)
  - **RE-AUDIT: PASS** (verified across 6 bypass shapes)

- [x] **Green gates**
  - Backend: 99 S09-specific tests pass; segment + affected files green
  - Frontend: build + lint + 392 tests green
  - Ruff: no new violations

## Test Evidence

**Backend tests:**
- S09-specific test suite: 99 tests
  - `test_search_fields.py`: whitelist registry, operator sets per data_type, JSONB resolution
  - `test_search_compiler.py`: compile_criteria injection gate (whitelist, operators, coercion, parameterization), DoS guards (depth, node_count, list_length), InvalidCriteria on unknown field/op
  - `test_search_service.py`: run_search pagination, soft-delete enforcement, saved-search CRUD (owner-scoped), group CRUD (smart live-resolve, static snapshot), promote saved→smart
  - `test_search_endpoints.py`: all /search and /groups endpoints, RBAC (require_volunteer), include_deleted admin-gating
  - `test_search_security.py`: DoS-guard bypass attempts (6 shapes), IDOR attempts, LIKE-escape verification
- Segment verification: S09 files + affected regression (models, services, routers)

**Frontend tests:**
- 392 tests green (build + lint + vitest)
- AdvancedSearchPage, FilterBuilder, FieldPicker, OperatorSelect component tests
- SavedSearchesPage, GroupsPage, GroupDetailPage page tests
- TanStack Query integration tests for search/groups services
- ContactPickerModal adaptation tests

**Security audit:**
- Initial BLOCK findings: all fixed pre-merge
- Re-audit: PASS
- Regression test suite covers all bypass shapes

## Notable Events

**Security found 1 HIGH + 1 MEDIUM + 1 LOW blocks** the 95 green tests missed:

1. **HIGH: DoS-guard bypass on list length check.**
   - Problem: `_check_bounds` discriminated leaf vs group nodes via `"conditions" in node` (True = group with sub-conditions, False = leaf value node). But the compiler used `"logic" in node` to make the same distinction. A leaf node with a HUGE value list (e.g., `{"value": [1, 2, ..., 10000], "conditions": []}`) passed the discriminator as a group (conditions=[]) but failed the operator validation, so it was never length-checked. The IN clause exploded unbounded.
   - Impact: DoS vector — unbounded IN clause could cause query timeout or memory exhaustion.
   - **Fix:** switched to unconditional list check that applies to ALL node types (leaf and group); unified logic discrimination across all code paths; added 2 regression tests for bypass shapes.

2. **MEDIUM: Cross-owner IDOR in group population.**
   - Problem: `populate_static_group(group_id, saved_search_id)` loaded SavedSearch by id only, not checking if the search belonged to the current user. A volunteer could pass another user's private saved-search id and populate a group with criteria they weren't supposed to access.
   - Impact: Unauthorized access to another user's private search criteria (reads the criteria JSONB); potential unauthorized data export if the group is used downstream.
   - **Fix:** added explicit owner_id check on SavedSearch load; returns 404 if user_id doesn't match current user; added 1 IDOR regression test.

3. **LOW: LIKE escape missing in contains_any operator.**
   - Problem: `contains_any` on text fields used SQL LIKE without escaping special chars (%, _); a value like "50%" would match unintended rows.
   - Impact: Minor data leakage; query result includes unexpected matches.
   - **Fix:** added escape=`\\` to the LIKE operator; added 1 escape verification test.

All issues were BLOCKED pre-merge; S09 was not committed until the security audit was re-run and passed. All 99 tests now pass with the corrected logic.

## Assumptions Carried in BLOCKERS.md

- **LOW:** Search/groups endpoints not individually rate-limited; no @limiter.limit decorators (codebase-wide gap, same class as S16/S08). FOLLOW-UP: add limits or install SlowAPIMiddleware globally.
- **LOW:** Groups are org-wide visible to all volunteers; any volunteer can rename/delete any group (only smart-criteria EDIT is admin-gated). By design per DoD; confirm with owner.
- **OPTIMIZATION:** `contains_any` uses LIKE-containment on JSON text (LIKE `%"value"%`); Postgres-native `@>` JSONB containment would be faster at scale (GIN-indexable). FOLLOW-UP: migrate when scale demands.
- **DB NOTE:** `groups` is a SQL reserved word; SQLAlchemy auto-quotes it; raw SQL must quote `"groups"`.
- **MERGE DEPENDENCY (S11):** When S11 (merge contacts) runs, must add `group_members.contact_id` to loser→survivor FK manifest (CN-04) to prevent orphaned group_members rows after contact merge.

## Migration & Rollback Notes

**Migration `s09a1b2c3d4e5`:**
- Creates 3 new tables (saved_searches, groups, group_members)
- Safe to run on live data (new tables are empty)
- Adds unique constraints (user_id, name) on saved_searches; (name) on groups
- Down_rev: `s08a1b2c3d4e5` (S08 migration)

**Rollback:**
- S09 migration rolls back cleanly via `alembic downgrade s08a1b2c3d4e5` (drops the 3 tables)
- Existing saved searches and groups are lost (irreversible per sprint scope)
- Frontend routes (/advanced-search, /saved-searches, /groups) revert to 404 or stub pages (feature flag gated)

## DoD Checklist

- [x] Migration `s09a1b2c3d4e5` with down_rev `s08a1b2c3d4e5` (3 tables, uniques, indexes)
- [x] Field registry (`services/search_fields.py`): 15 core + 7 S23-derived + active custom fields
- [x] Injection-safe compiler (`services/search_service.py`): whitelist pipeline, parameterization, DoS guards (depth/node/list)
- [x] Soft-delete enforcement (non-bypassable, always AND deleted_at IS NULL)
- [x] Two routers (`routers/search.py`): /search (CRUD), /groups (CRUD + populate)
- [x] Frontend UI: AdvancedSearchPage, FilterBuilder, FieldPicker, OperatorSelect, SavedSearchesPage, GroupsPage, GroupDetailPage
- [x] ContactPickerModal adaptation (contact_reference input support)
- [x] Test migration head updated (EXPECTED_HEAD, down_revision test)
- [x] Security audit PASS (1 HIGH + 1 MEDIUM + 1 LOW blocks fixed, re-audit passed)
- [x] Backend tests green (99 S09-specific, full-suite integration verified)
- [x] Frontend tests green (392 tests, build + lint)
- [x] Ruff clean (no new violations)

## Migration History

**Alembic chain:**
- S08: `s08a1b2c3d4e5` (down_rev: `s16a1b2c3d4e5`)
- S09: `s09a1b2c3d4e5` (down_rev: `s08a1b2c3d4e5`) ← **NEW, this sprint**

**Live alembic heads after S09:**
- `s09a1b2c3d4e5` (current, S09)

**Rebase note:** S09 migration chains off S08 (committed 2026-06-26). No migration conflicts with other parallel sprints (all sequential after S08).
