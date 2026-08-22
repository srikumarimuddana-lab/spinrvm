# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-22 |
| Author | Claude Code |
| Surface(s) | docs (`.claude/context/regulatory-sk.md`, root `CLAUDE.md`, `docs/runbooks/data-retention.md`) |
| Domain (Sentry tag) | safety / rides |
| PR / commit link | (this branch: `claude/fix-b23-rider-identity-doc`) |
| Related issue or gap ID | ACTION_ITEMS.md B23 |

## 1. Issue / gap identified

`.claude/context/regulatory-sk.md`'s trip-log retention table promised "Rider
identity linked to trip: 7 years (hashed after 2)" — a general rule for
every ride, not just DSAR-deleted accounts. Nothing in `purge_pii_retention()`
or anywhere else in the codebase implements a 2-year hashing step. The same
gap was cross-referenced (accurately, as a known gap) from root `CLAUDE.md`
and `docs/runbooks/data-retention.md`.

## 2. Root cause

The doc's promise was never accurate to what the product implements or
plans to implement. Per explicit user decision (option (c) of B23's three
offered options), the fix is to correct the doc to match reality rather
than build the hashing step — the literal fix would break every active
rider's own "my trips" screen and any admin/support/refund lookup by rider,
since `rides.rider_id` is the same FK those paths join on. Hashing/nulling
it at 2 years for every ride, not just DSAR-requested ones, is a live,
real-user-facing regression, not a narrow backend fix — exactly the
reasoning B23 itself laid out for why this needed a decision rather than an
agent's unilateral call.

## 3. Fix / remediation

Docs-only change across three files:
- `.claude/context/regulatory-sk.md`: the retention table's "Rider identity
  linked to trip" row changed from the false "hashed after 2" promise to
  "7 years, kept fully attributable (`rider_id` not hashed/nulled at any
  point during the window)", with a note explaining the correction and
  pointing at the product's actual, already-documented retention model
  (migration 216/289's "Uber/Lyft attributable retention").
- `CLAUDE.md`'s Compliance → Deletion bullet: updated from "not yet
  implemented, tracked as B23" to reflect that B23 is now resolved by
  correcting the doc, not by implementing hashing.
- `docs/runbooks/data-retention.md`: updated its "Known gap, not
  implemented" note to "Resolved 2026-08-22", and corrected a stale
  cross-reference (it pointed at ACTION_ITEMS.md B18, which is the
  anonymize-vs-delete recorded decision this same gap was originally split
  out from — the actual tracking item is B23).

No application code changed. No hashing step was built — this was a
deliberate choice not to build one, matching option (c) of the three
choices B23 laid out.

## 4. Risk & impact on existing functionality

- **Blast radius: zero application-code impact.** Grepped the whole repo
  (`.md` files) for every other instance of the stale "hashed after 2"
  claim — found and corrected all three (the fourth hit, `ACTION_ITEMS.md`
  itself, is the tracking entry being closed in the same pass, not a
  doc making the promise).
- This does not change what `purge_pii_retention()` or any other code path
  actually does — nothing was hashing rider identity before this change,
  and nothing hashes it after. The only change is that the documentation
  now says so, instead of promising a step that was never built.
- No interaction with any live-tested surface (rides, payments, auth,
  corporate, safety) beyond correcting what these reference docs say about
  retention — the actual retention behavior (7-year attributable window,
  then hard-delete per migration 216/289) is unchanged.

## 5. User-experience effect

None — documentation-only change, not user-facing. Does not change any
rider's actual data-retention experience, since no hashing was ever
implemented to remove.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `.claude/context/regulatory-sk.md` | Retention table row corrected + explanatory note added | Resolve B23 per user-approved option (c) |
| `CLAUDE.md` | Compliance → Deletion bullet updated to reflect B23 resolved | Keep cross-reference accurate |
| `docs/runbooks/data-retention.md` | "Known gap" note updated to "Resolved", cross-reference corrected B18→B23 | Keep cross-reference accurate |
| `docs/change-log/2026-08-22-fix-b23-rider-identity-doc.md` | New change-log entry | Required per CLAUDE.md |
| `ACTION_ITEMS.md` | B23 marked closed | Track progress |

## 7. Before / after

```diff
-| Rider identity linked to trip | 7 years (hashed after 2 — **not yet implemented, ACTION_ITEMS.md B23**) | Balance audit vs. privacy |
+| Rider identity linked to trip | 7 years, kept fully attributable (`rider_id` not hashed/nulled at any point during the window) | Balance audit vs. privacy — see note below |
```

## 8. Rollback plan

`git revert` — pure documentation change, no live-data footprint, no
migration, no application code. Reverting restores the (inaccurate) prior
promise text, which itself was the thing being fixed — a revert here would
be undoing a correction, not undoing a risk.

## 9. Verification performed

- [x] Grepped the entire repo's `.md` files for every instance of "hashed
  after 2" / "rider identity hashed" — found and corrected all three
  documents that stated the claim (the fourth hit is `ACTION_ITEMS.md`'s
  own B23 tracking entry, updated separately below).
- [x] Explicit user decision obtained before writing (option (c) of B23's
  three offered choices), per this repo's own rule that a
  regulatory/compliance-doc correction needs the same sign-off any other
  windows-table change would.
- [x] Confirmed the product's actual retention model this doc now points
  at (migration 216/289's "Uber/Lyft attributable retention") is itself
  already a recorded decision (ACTION_ITEMS.md B18) — not inventing a new
  policy, just correcting a stale doc to point at an existing one.
- [ ] No automated test applicable — prose/table edits in markdown
  reference docs with no corresponding test suite.

## 10. What was NOT verified

- Did not audit whether any document *outside* this repo (an external
  compliance filing, a privacy policy draft, a slide deck) repeats the
  stale claim — only this repo's own markdown files were checked.
- Did not re-open or re-litigate the anonymize-vs-delete question (B18) or
  the rideless-SOS-path question (B15(c)) — those are separate, still-open
  decision items, not touched by this pass.
