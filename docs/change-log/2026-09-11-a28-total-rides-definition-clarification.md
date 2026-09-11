# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-11 |
| Author | Claude Code (session `session_013hMEsuEPVu7gwAk21XB4hF`) |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | [#5235](https://github.com/srikumarimuddana-lab/spinrvm/pull/5235) |
| Related issue or gap ID | `ACTION_ITEMS.md` A28 |

## 1. Issue / gap identified

An audit (Phase 3 cross-surface finding #10) flagged that admin's per-rider
"total rides" count and rider-app's own "total rides" stat use different
definitions, with no code comment anywhere explaining that the difference
is intentional — a reader could reasonably assume it's a bug.

## 2. Root cause

The three call sites were each written independently, for different
purposes, with no cross-reference to each other and no comment stating
their scope was a deliberate choice rather than an oversight:
- `backend/routes/admin/users.py`'s per-rider admin view counts **all**
  ride statuses (including cancelled/no-show) — an operational need, so
  admins can see the full picture, not just completed trips.
- `backend/routes/auth.py`'s `GET /me` (feeds `account.tsx`'s rider-facing
  "Rides" hero stat) counts **completed-only**, lifetime — a rider-facing
  "how many trips have I taken" number.
- `backend/routes/rides/queries.py`'s `GET /rides/stats` (feeds
  `activity.tsx`'s "Trips" stat) counts **completed-only**, but scoped to
  a selectable period (today/week/month/all) — a different, narrower
  question ("how many trips in this window").

Re-grounding against current code during this investigation found the
original audit's framing (a simple 2-way "admin vs rider-app" split) was
itself incomplete — it's a 3-way split, since rider-app shows two
different numbers on two different screens, not one.

## 3. Fix / remediation

No behavior change. Added a one-line comment at each of the 3 call sites
stating its exact scope (all-status/lifetime, completed-only/lifetime,
completed-only/period-scoped) and cross-referencing the other two, so a
future reader doesn't need to re-derive this from scratch or assume it's
a bug. Asked the user for a decision among 4 options (document only /
align rider-app's own two numbers / full 3-way reconciliation / no
action) — they chose **document only, no behavior change**, matching the
audit's own suggested low-risk default. Decision recorded in
`.claude/context/memory.md`.

## 4. Risk & impact on existing functionality

- **None.** This is a comment-only change — zero lines of executable code
  were added, removed, or reordered at any of the 3 sites.
- Verified via `ruff check` and `ruff format --check` on all 3 touched
  files: clean, no diff beyond the added comment lines.
- Blast radius: **isolated** — comments are not read at runtime; no other
  code path, table, background loop, or money/wallet flow is affected.
- No interaction with the ride state machine, background loops, or
  money/wallet deltas.

## 5. User-experience effect

**None.** No rider, driver, corporate admin, or internal admin sees any
difference — the 3 `total_rides` numbers return exactly the same values,
computed by exactly the same queries, as before this change. No copy or
notification changed.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/admin/users.py` | One-line comment above the `total_rides` field explaining its all-status/lifetime scope | Document intentional difference |
| `backend/routes/auth.py` | One-line comment above the `GET /me` `total_rides` assignment explaining its completed-only/lifetime scope | Document intentional difference |
| `backend/routes/rides/queries.py` | One-line comment above `GET /rides/stats`'s `total_rides` computation explaining its completed-only/period-scoped scope | Document intentional difference |
| `ACTION_ITEMS.md` | A28 marked closed with the corrected 3-way definition recorded | Backlog accuracy |
| `.claude/context/memory.md` | New entry recording the decision | Cross-session decision log |

## 7. Before / after

Not applicable — pure comment addition, no behavior-changing diff. Example
(one of the three sites):

```python
# Before
total_rides = len(rides)

# After
# ACTION_ITEMS.md A28: completed-only (see `filters` above) AND
# period-scoped (today/week/month/all, per the `period` query param) —
# distinct from auth.py's GET /me (completed-only but always lifetime)
# and admin/users.py's all-status lifetime count. period=all happens to
# equal GET /me's number; the other periods don't. Confirmed intentional
# 2026-09-11 — see .claude/context/memory.md.
total_rides = len(rides)
```

## 8. Rollback plan

`git revert` — this is purely additive comment text with no runtime
effect and no data written anywhere; a plain code revert is a complete
and sufficient rollback (unlike a money/ride-state change, there is no
live-data remediation needed either direction).

## 9. Verification performed

- [x] Automated tests run: none required (no logic changed); confirmed no
  existing test asserts on comment content
- [ ] Manual repro steps followed in staging — n/a, no behavior to repro
- [x] Blast-radius grep performed: confirmed each of the 3 `total_rides`
  call sites is read-only by its own caller (no shared helper function
  touched), and no other code references these specific comment blocks
- [x] Reviewed against relevant `CLAUDE.md` convention(s): "Escalate,
  don't silently ship" (product decision obtained via `AskUserQuestion`
  before implementing); Change Impact Log requirement (this document)
- [x] Feature-flagged if user-visible and non-trivial — n/a, not
  user-visible at all

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed: isolated, comment-only
- [x] No silent behavior change to an already-shipped flow — there is no
  behavior change of any kind in this diff
