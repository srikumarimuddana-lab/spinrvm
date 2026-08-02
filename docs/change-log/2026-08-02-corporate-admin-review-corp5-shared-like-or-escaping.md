# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-02 |
| Author | Claude Code |
| Surface(s) | backend |
| Domain (Sentry tag) | corporate |
| PR / commit link | branch `claude/spinrvm-schedule-ride-review-2jsank` |
| Related issue or gap ID | Corporate + Admin Portal Review — Corporate #5 |

## 1. Issue / gap identified

`repositories/corporate_repo.py`'s `get_all_corporate_accounts` and
`list_corporate_accounts_filtered` each hand-rolled their own ILIKE/OR
escaping instead of using the shared `_escape_like`/`_build_or_clause`
helpers in `repositories/_base.py` that every other repo module's
`$or`/`$regex` search goes through. Their escaping strategy was also
weaker: after escaping LIKE wildcards (`%`, `_`), they **stripped**
`,`, `.`, `(`, `)` entirely from the search term via
`re.sub(r"[,\.\(\)]", "", safe)` — a legitimate search like
`"Acme, Inc"` silently became `"Acme Inc"` before ever reaching the
database, changing what the admin searched for with no indication it
happened.

## 2. Root cause

These two functions predate (or were never migrated to) the
`_apply_filters`/`_build_or_clause` shared-filter infrastructure that
CLAUDE.md's query-filter conventions describe as the single place LIKE-
wildcard escaping and PostgREST quoting are supposed to live. Because
they build the `.or_(...)` string by hand and call it directly on the
Supabase query builder (rather than going through `get_rows`/
`_apply_filters`), the duplication was never caught by the shared
helper's own test coverage — each copy had to independently get the
escaping right, and the stripping approach was is a different (and
weaker) mitigation than the shared helper's escape-and-quote approach.

## 3. Fix / remediation

- Imported `_apply_filters` from `repositories/_base.py` (dual-import,
  both branches) alongside the module's other existing shared-helper
  imports (`_rows_from_res`, `_serialize_for_api`, `_single_row_from_res`
  — already imported the same way).
- Replaced both hand-rolled `safe = ...; query.or_(f"...")` blocks with
  `query = _apply_filters(query, {"$or": [{"col": {"$regex": search,
  "$options": "i"}}, ...]})` — the exact Mongo-shaped `$or` filter dict
  every other repo module already uses for the same purpose.
- Removed the now-dead `import re` (only used by the deleted
  strip-regex).
- No behavior change to any other part of either function — `is_active`/
  `status`/`size_tier` filtering, ordering, and pagination are untouched.

## 4. Risk & impact on existing functionality

- **Blast radius: 2 functions, both in `corporate_repo.py`.** Grepped
  every caller of `get_all_corporate_accounts` and
  `list_corporate_accounts_filtered` — both are re-exported through
  `db_supabase.py` and consumed by the admin corporate-accounts list
  endpoint(s); neither function's return shape or other parameters
  changed.
- **Behavior change: search terms containing `,`/`.`/`(`/`)` now match
  correctly instead of having those characters silently dropped.** This
  is the entire point of the fix — an admin searching for
  `"Acme, Inc."` previously got results for `"Acme Inc"` (comma and
  trailing period silently removed) with no way to know the search was
  altered; now the literal term is escaped and matched.
- Added 2 new tests directly asserting the string actually passed to
  the mocked `.or_(...)` call for a search term containing a comma —
  confirming the comma is now backslash-escaped (`\,`) rather than
  absent, and that the query pattern syntax is the shared helper's
  `*value*` form (not the old hand-rolled `%value%`).
- Ran the full dependent test suite (`test_corporate_db_helpers.py`,
  `test_corporate_admin_routes.py` — 33 tests) — all passing, including
  the pre-existing `test_list_companies_by_status_filter` (search=None,
  so the changed code path isn't exercised by that test but confirms no
  regression to the non-search filter path).

## 5. User-experience effect

**Internal admin-facing only.** An admin searching the corporate-accounts
list by company/contact name or email now gets accurate matches for
search terms containing a comma, period, or parenthesis instead of
having those characters silently stripped before the search ran. No
change to searches without those characters (the common case), which
already worked correctly before this fix.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/corporate_repo.py` | Both search-filter call sites now route through `_apply_filters`'s `$or`/`$regex` handling instead of hand-rolled escape+strip; removed the now-unused `import re` | Use the single source of truth for LIKE/OR escaping instead of a weaker, duplicated implementation |
| `backend/tests/test_corporate_db_helpers.py` | 2 new tests asserting the exact `.or_()` string for a comma-containing search term | Lock in that the shared helper's escape-not-strip behavior is actually wired in |

## 7. Before / after

```python
# Before — comma silently stripped
safe = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
safe = re.sub(r"[,\.\(\)]", "", safe)
query = query.or_(f"name.ilike.%{safe}%,contact_name.ilike.%{safe}%,contact_email.ilike.%{safe}%")
```

```python
# After — comma escaped, term preserved
query = _apply_filters(
    query,
    {
        "$or": [
            {"name": {"$regex": search, "$options": "i"}},
            {"contact_name": {"$regex": search, "$options": "i"}},
            {"contact_email": {"$regex": search, "$options": "i"}},
        ]
    },
)
```

## 8. Rollback plan

Plain code change, no migration, no data written differently — this
only changes how a search-term string is escaped before being sent to
PostgREST. `git revert` fully restores the prior (stripping) behavior.
No feature flag — this replaces a weaker, duplicated implementation with
the codebase's own established single source of truth for the same
operation; there's no meaningful dark-ship version of "stop silently
corrupting search terms."

## 9. Verification performed

- [x] Automated tests: `test_corporate_db_helpers.py` (8, incl. 2 new),
      `test_corporate_admin_routes.py` (25) — 33 passed, run via the
      session's `/tmp/spinr_venv` venv from repo root.
- [x] `ruff check` on both touched files — clean (confirmed the
      now-unused `import re` was correctly removed, not just unused).
- [x] Blast-radius grep performed (see §4): every caller of both
      functions.
- [ ] Manual repro in staging — not performed, no staging access.
- [x] Dry-run scenario: an admin searches the corporate-accounts list
      for `"Acme, Inc"`. Before this fix: the comma is stripped before
      querying, so the search effectively runs as `"Acme Inc"` — a
      company literally named `"Acme, Inc"` would still match (ILIKE
      wildcard matches substrings), but a company named exactly
      `"Acme Inc"` (no comma) would ALSO match a search the admin typed
      with a comma, which isn't what they searched for. After this fix:
      the comma is escaped and preserved as a literal character in the
      match, so the search behaves exactly as typed.

## 10. Sign-off

- [x] Rollback plan is concrete and testable
- [x] Blast radius is stated, not assumed — both functions' callers
      grepped, both dependent test files run
- [x] No silent behavior change to a working flow for the common case
      (search terms without reserved characters) — verified by the
      existing `test_list_companies_by_status_filter` passing unmodified
      and by the new tests confirming only the escaping mechanism
      changed, not the match semantics for ordinary terms

## What was NOT verified

Not tested against a live/staging Supabase — only mocked query-builder
call arguments. Did not audit the rest of the codebase for other
hand-rolled ILIKE/OR escaping outside `corporate_repo.py` — the review
finding named this one file specifically; a broader sweep for the same
anti-pattern elsewhere is a reasonable follow-up, not performed here.
