# Change Impact & Risk Log — admin driver search query design

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code (session `claude/admin-driver-search-design-ryh7yc`) |
| Surface(s) | backend (admin API). No frontend change — `admin-dashboard` was already wired correctly. |
| Domain (Sentry tag) | `admin` (primary), `drivers` |
| PR / commit link | `d8548d2`, `4493929`, `314c8db` on `claude/admin-driver-search-design-ryh7yc` |
| Related issue or gap ID | Reported in live app testing: searching the admin drivers list for driver "Nighil" returns nothing |

## 1. Issue / gap identified

Searching the admin panel's driver list by name returned no rows for a driver
that exists. The same defect silently affected admin **ride** search by rider or
driver name, and a second, independent defect broke any search term containing a
space, hyphen, period or `+` across the users, drivers, promotions, safety and
audit-log search boxes.

## 2. Root cause

Three distinct causes, in the shared query layer rather than the route:

**(a) `$in` was silently dropped inside `$or` — this is the reported bug.**
`backend/repositories/_base.py::_build_or_clause_term` translates one
`{col: predicate}` pair into a PostgREST `or()` leaf. It had branches for
`$regex`, `$ne`, `$gt`, `$gte`, `$lt`, `$lte` and scalars, and `return None` for
everything else; `_build_or_clause` then skipped any `None`.

A driver's display name is not on the `drivers` table — it lives on the joined
`users` row, and PostgREST cannot filter a parent by an embedded child. So
driver search resolves the term against `users` first and matches drivers with
`{"user_id": {"$in": matched_ids}}`. That leaf — the only one that can ever match
a name — hit the `return None` path and vanished. The query that actually reached
Postgres was:

```
id.ilike.*Nighil*,phone.ilike.*Nighil*,license_plate.ilike.*Nighil*,driver_code.ilike.*Nighil*,user_id.ilike.*Nighil*
```

No plate, phone or UUID contains "Nighil", so the result was empty. There was no
error and no log line — the filter just wasn't there.
`routes/admin/rides.py::_build_rides_filters` builds the same shape for
rider/driver name search and had the same silent failure.

**(b) `re.escape()` applied to values used as SQL LIKE patterns.** A `$regex`
predicate compiles to `ILIKE '%term%'`, not to a regex. Callers ran the term
through `re.escape()` first, so `re.escape("Nighil Kumar")` produced
`Nighil\ Kumar`. In the `$or` path that escape survived into the LIKE pattern; in
the non-`$or` path `_escape_like` then doubled the backslash into `\\`, making
the pattern match a literal backslash — never a row. Most real names and every
formatted phone number contain an affected character.

**(c) Inconsistent escaping between the two filter paths.** `_apply_filters`
escapes SQL LIKE wildcards via `_escape_like` (the C6 fix); the `$or` path never
did. A `%` typed into any admin search box matched every row, and `_` matched any
character.

Design gaps found alongside the bugs: a multi-word name was ILIKE'd whole against
`first_name` and `last_name` individually so it matched neither column; the
`drivers.name` mirror was not searched (a stale code comment claimed the column
did not exist — it does); the users pre-query truncated at 100 rows with no
signal; and a formatted phone never substring-matched the stored E.164 value.

## 3. Fix / remediation

- **Query layer** (`_base.py`): added `$in` (and `$notnull`) to the `or()` leaf
  builder; **raise** on any predicate it cannot express instead of returning
  `None`; raise in `_apply_filters` when a non-empty `$or` flattens to zero
  terms; apply `_escape_like` in the `$or` path so it matches the non-`$or` path;
  quote values containing `,` `(` `)` `"` `\` so a comma in a search term no
  longer truncates the whole or-group.
- **Callers**: pass the raw term; the query layer owns all escaping.
- **Driver search**: AND whitespace tokens (each ORed across the user columns) so
  full names match; search the `drivers.name` mirror; digits-only fallback for
  formatted phones; users pre-query cap 100 → 500 with a warning on truncation;
  term length and token count bounded; `id`/`user_id` only searched for a term
  shaped like a pasted ID (≥ 8 chars, no whitespace) so a short term stops
  coincidence-matching UUID substrings.

## 4. Risk & impact on existing functionality

**Blast radius: cross-surface within the backend — `_apply_filters` /
`_build_or_clause` are the shared filter translator for every `db_supabase`
read and write.** Enumerated, not assumed.

Every non-test `$or` construction (`grep -rn '"\$or"' routes/ services/ utils/
repositories/`):

| Consumer | Predicates used | Effect of this change |
|---|---|---|
| `routes/admin/drivers.py` | `$regex`, `$in` | **Fixed** — the reported bug |
| `routes/admin/rides.py:159-161` | `$regex`, `$in` | **Fixed** — same silent drop, rider/driver name search |
| `routes/admin/users.py:95` | `$regex` | Escaping only (b/c) |
| `routes/admin/promotions.py:117-147` | `$regex`, `null`, `$gte`, `$lt` | Escaping only; its existing `$and`-of-`$or` shape is unchanged and is the precedent this change relies on |
| `routes/admin/maintenance.py:304` | `$regex` | Escaping only |
| `routes/admin/data_transfer_search.py:57-58` | `$regex` | Escaping only (already passed raw input) |
| `routes/admin/rides.py:2820` | `$gte` on ISO timestamps | Unchanged — `:` and `.` are not quoted, verified against `_postgrest_or_value` |
| `utils/document_expiry.py:213` | raw PostgREST string, not a dict | Untouched — bypasses this builder entirely |

Two risks were specifically considered:

1. **Raising instead of returning `None`** could 500 a previously-"working"
   endpoint. Every current caller was enumerated above and none passes an
   unsupported predicate. The alternative is worse than a 500: `_apply_filters`
   is shared with `update_one`/`delete_many`, so an `$or` whose terms were all
   dropped applied **no filter at all** — a delete would have matched the entire
   table. This is a latent data-loss guard, not just a search fix.
2. **Quoting `eq`/`neq`/comparison values** could change generated clauses.
   Mitigated by quoting only when the value contains `,` `(` `)` `"` `\` — for
   every other value the emitted string is byte-identical to before. Verified
   against the ISO-timestamp consumer above.

No interaction with the ride state machine, money/wallet deltas, dispatch, or any
of the 16 background loops in `core/lifespan.py`: this path is admin-read-only
search. No migration, no schema change, no table written.

## 5. User-experience effect

- **Internal admin**: driver and ride search by name now returns results where it
  previously returned an empty list. Search terms containing a space, hyphen or
  period now work across the users/drivers/promotions/safety/audit-log boxes.
  Formatted phone numbers now match. This is a fix to a broken flow, not a change
  to a working one — no admin retraining needed and no copy change.
- **Rider / driver / corporate admin**: no effect. No customer-facing surface,
  notification or copy is touched.
- **Mid-session visibility**: none. Nothing here is reachable by a rider mid-ride
  or a driver online.
- **Two behavior changes to flag**, both narrowing results that were previously
  over-broad. Neither is a new feature, but an admin who had adapted to the old
  behavior would notice:
  1. A `%` or `_` typed into an admin search box previously acted as a SQL
     wildcard (matching everything, or any character). It now matches literally.
  2. A term shorter than 8 characters (or containing a space) no longer
     substring-matches the `drivers.id` / `drivers.user_id` columns. Previously
     searching "ab" returned every driver whose UUID happened to contain "ab".
     Pasting a full ID still works.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/repositories/_base.py` | `$in`/`$notnull` in `_build_or_clause_term`; raise on unsupported predicate; `_escape_like` in the `$or` path; `_postgrest_or_value` quoting; raise on an all-empty `$or` in `_apply_filters` | Root cause (a) and (c) |
| `backend/routes/admin/drivers.py` | Search-design block, `_driver_search_tokens`, `_phone_digits`, `_resolve_driver_search_user_ids`; rewritten search filter | Root cause (b) + the design gaps |
| `backend/routes/admin/users.py` | Dropped `re.escape()` (5 sites) | Root cause (b) |
| `backend/routes/admin/promotions.py` | Dropped `re.escape()` (2 sites) | Root cause (b) |
| `backend/routes/admin/safety.py` | Dropped `re.escape()` | Root cause (b) |
| `backend/routes/admin/maintenance.py` | Dropped `re.escape()` | Root cause (b) |
| `backend/tests/test_db_supabase_helpers.py` | 8 new query-layer cases; `test_unknown_dict_returns_none` → `test_unknown_dict_raises` | Layer-level regression guard |
| `backend/tests/test_admin_driver_search.py` | New — 22 cases | Route-level regression guard |
| `backend/tests/test_admin_users_search.py`, `test_admin_safety_incidents.py`, `test_admin_extended.py` | 3 assertions updated from the escaped to the raw term | They asserted the `re.escape` output and had frozen bug (b) in place |

## 7. Before / after

Query layer — the leaf that carries a name match:

```python
# Before — _build_or_clause_term
        if "$lte" in val:
            return f"{col}.lte.{_unwrap_enum(val['$lte'])}"
        return None          # <-- {"user_id": {"$in": [...]}} landed here and vanished
```

```python
# After
        if "$in" in val:
            values = val["$in"]
            if not isinstance(values, (list, tuple)):
                raise ValueError(...)
            if not values:
                return None          # empty IN matches nothing -> contributes nothing to an OR
            return f"{col}.in.({','.join(_postgrest_or_value(v) for v in values)})"
        ...
        raise ValueError(f"$or term {col!r}: unsupported predicate {val!r} ...")
```

Generated PostgREST clause for `search=Nighil` (users pre-query matched `uid-nighil`):

```
# Before — the name match is absent; nothing matches
id.ilike.*Nighil*,phone.ilike.*Nighil*,license_plate.ilike.*Nighil*,driver_code.ilike.*Nighil*,user_id.ilike.*Nighil*

# After
id.ilike.*Nighil*,user_id.ilike.*Nighil*,name.ilike.*Nighil*,phone.ilike.*Nighil*,license_plate.ilike.*Nighil*,driver_code.ilike.*Nighil*,user_id.in.(uid-nighil)
```

Multi-word name against `users`:

```python
# Before — neither column contains the whole string, so "Nighil Kumar" matched nothing
{"$or": [{"first_name": {"$regex": "Nighil Kumar", ...}}, {"last_name": {"$regex": "Nighil Kumar", ...}}, ...]}

# After — tokens ANDed, each ORed across the user columns
{"$and": [
    {"$or": [{"first_name": {"$regex": "Nighil", ...}}, {"last_name": {"$regex": "Nighil", ...}}, ...]},
    {"$or": [{"first_name": {"$regex": "Kumar",  ...}}, {"last_name": {"$regex": "Kumar",  ...}}, ...]},
]}
```

## 8. Rollback plan

`git revert 314c8db 4493929 d8548d2` (in that order) is a **complete** rollback
here, and the usual caveat does not apply: this change writes nothing. No
migration, no schema change, no `app_settings` value, no row touched — it only
changes how a read query is built, so reverting restores the previous behavior
exactly with no data-level remediation.

No feature flag was added. Justification: the change is a fix to a flow that is
currently broken (empty results), on an internal-admin read-only surface, with no
customer-facing component. Flagging it would mean shipping a flag whose "off"
position is the known-broken state. Per `CLAUDE.md` release gate 3 the flag
requirement targets user-visible, non-trivial *new* UX; this is neither.

Note for the revert path: reverting `d8548d2` also restores the silent-drop
behavior in `_apply_filters` for update/delete `$or` filters. No current caller
relies on it, but the revert is a strict return to the prior risk, not a neutral
one.

## 9. Verification performed

- [x] **Automated tests run** — unit. Full backend suite at HEAD:
      **5735 passed, 8 skipped, 1 xfailed, 0 failed** (240s). Baseline at
      `HEAD~5` (pre-change): **5704 passed, 8 skipped, 1 xfailed, 0 failed**.
      The delta is exactly the 31 tests this change adds (22 in
      `test_admin_driver_search.py`, 9 in `test_db_supabase_helpers.py`), so no
      pre-existing test changed state.
- [x] **One intermittent failure investigated and attributed, not waved off** —
      an earlier full-suite run failed
      `test_compliance_reports_http.py::test_knight_archer_report_filters_by_status`.
      It passes 5/5 in isolation and passed the next full-suite run at the
      identical commit. Root cause: several *pre-existing* test files leave
      `AsyncMock` coroutines un-awaited, and pytest 9's
      `_pytest/unraisableexception` plugin **re-raises** those errors
      (`raise errors[0]`) against whichever test is running when the GC collects
      them — so blame lands on an innocent test and moves whenever test count or
      ordering changes. Confirmed pre-existing: `test_ride_accept_flow.py` +
      `test_drivers_extended.py` emit 7 such warnings on an unmodified baseline
      checkout, while `test_admin_driver_search.py` (this change's new file)
      emits **0**. This change adds no leaks; it shifted GC timing by adding
      tests ahead of the compliance file alphabetically. Logged as
      `ACTION_ITEMS.md` A8 rather than re-diagnosed every session.
- [x] **Regression tests proven to fail pre-fix** — `_base.py` was reverted to
      its pre-fix state and `test_name_search_renders_the_in_leaf_into_the_postgrest_clause`
      failed, confirming the test reproduces the reported bug rather than
      restating the new code.
- [x] **Blast-radius grep performed** — `grep -rn '"\$or"'` and
      `grep -rn 'ai\.escape'`/`re.escape` across `routes/ services/ utils/
      repositories/`; `grep -rn '_build_or_clause'` for consumers of the changed
      helper. Results tabulated in §4.
- [x] **Reviewed against `CLAUDE.md` conventions** — "Do not silently swallow
      errors" (drove the raise-instead-of-drop decision); PIPEDA (the truncation
      warning logs a token count, never the search term — names, phones and
      emails are all forbidden in logs, and there is a test asserting the term
      does not appear); observability (`extra={"domain": "admin", "surface":
      "backend"}` per the Sentry-tag convention).
- [x] **PostgREST encoding verified against the real client**, not assumed —
      confirmed with `postgrest-py` that repeated `or_()` calls emit two `or=`
      params (which PostgREST ANDs, making the `$and`-of-`$or` token design
      valid) and that `in_` renders `in.(a,b)` with reserved-character quoting
      matching `_postgrest_or_value`.
- [x] **Schema claims verified** — `drivers.name`, `drivers.id` and
      `drivers.user_id` confirmed `TEXT` in `backend/supabase_schema.sql`. The
      `id`/`user_id` ILIKE leaves are therefore valid (a `uuid` column would have
      needed a cast), and `name` exists despite the code comment that said it
      did not.
- [x] **Lint** — `ruff check` and `ruff format --check` clean on all changed
      files. The 19 pre-existing `ruff` errors elsewhere in `backend/` are in
      unrelated test files and were left alone.
- [ ] **Feature-flagged** — not applicable, see §8.

## 10. What was NOT verified

- **Not tested against a live or staging Supabase.** All tests mock
  `db_supabase.get_rows`, so what is verified is the *filter dict* each route
  builds and the *PostgREST clause string* the layer renders from it — not that
  Postgres returns the expected rows. The clause was checked against the real
  `postgrest-py` encoder, but no query was executed against a real database. The
  specific claim that would benefit most from a staging check: that
  `first_name.ilike.*X*` ANDed via two `or=` params returns the intended rows on
  the real `users` table.
- **No manual repro in the admin UI.** The original report ("searching Nighil
  shows nothing") was diagnosed from the code path and reproduced at the query
  layer, not by driving the dashboard against real data. Recommend one manual
  check of `/dashboard/drivers` searching "Nighil" before merge.
- **No production build run.** This change touches only `backend/`; no
  `admin-dashboard` file was modified, so `npm run build` was not applicable and
  was not run.
- **`drivers.driver_code` existence not independently confirmed** in the schema
  file. It was carried forward from the existing search filter unchanged, so it
  is no more or less correct than before this change.
- **Postgres `ESCAPE` semantics for a backslash preceding a non-wildcard char**
  were reasoned about from the LIKE implementation, not executed. This only
  affects how the *old* `re.escape` output behaved and does not gate the fix —
  the fix removes those backslashes at the source.
- **Search result relevance/ranking is unchanged and unmeasured.** Results come
  back in the list's sort order (default `created_at DESC`), not by match
  quality, so an exact name match can still sit below an incidental substring
  match. Out of scope here; noted as a follow-up.
- **The suite's own reliability as a merge gate** is limited by `ACTION_ITEMS.md`
  A8 (leaked un-awaited coroutines → GC-timed blame under pytest 9). This change
  is green over a full run and adds no leaks, but a future green run on this
  branch is weaker evidence than it looks until A8 is closed.
- **Pagination interacts with the post-query dedup** (`admin_get_drivers` dedups
  by `user_id`/`phone` *after* the DB `LIMIT`), so a page can render fewer than
  `limit` rows. Pre-existing, untouched by this change, and not fixed here.
