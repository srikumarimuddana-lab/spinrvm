# RICE Scoring Template

> Copy this into a new `docs/audit/YYYY-MM-DD-rice-scoring-<slug>.md` when
> prioritizing a batch of backlog items (e.g. `ACTION_ITEMS.md`'s P3/P4
> sections, a sprint candidate list, a design-system adoption backlog).
> Extracted from the first application of this method to Spinr's backlog —
> see `docs/audit/2026-09-14-rice-scoring-p3-p4-backlog.md` for a worked
> example with 32 real items scored.

## Header block

| Field | Value |
|---|---|
| Date | |
| Author | |
| Source | Where the candidate items came from (file + line range, or query) |
| Scope decision | Is this a companion document (informational, not editing the source backlog file) or a direct edit? State it — editing a large, actively-changing shared file (e.g. `ACTION_ITEMS.md`) to inject scores risks a needless merge conflict for a purely additive pass; prefer a companion doc unless a maintainer asks for scores folded in directly. |

## Why RICE, and what it does not tell you

RICE = (Reach × Impact × Confidence) / Effort. It is a **sequencing
heuristic, not a measurement** — every score below comes from one reviewing
pass reading each item's own written description once. It does not consult
analytics, support-ticket volume, telemetry, or the item's actual owner.
Treat every table below as a first-pass ranking to sanity-check, not a
decision handed down.

## The four inputs — use these scales, don't invent new ones each time

**Reach** — how much of the platform/user base this touches. For a genuine
user-facing feature, use a real count (e.g. "rides/month", "drivers
affected"). For backend/infra/tooling items with no clean per-user count,
use this 1–8 ordinal blast-radius scale instead, and say so:

| Reach | Meaning |
|---|---|
| 8 | Whole platform / every active user or every request |
| 6–7 | Most of one major surface (e.g. all drivers, all dispatch traffic) |
| 4–5 | A meaningful subset (one flow, one service area, one admin role) |
| 2–3 | A narrow slice (one edge case, one rarely-hit code path) |
| 1 | Isolated (one table, one dev-only tool, no live traffic) |

**Impact** — the classic Intercom RICE scale. Do not use arbitrary decimals:

| Impact | Meaning |
|---|---|
| 3 | Massive impact |
| 2 | High impact |
| 1 | Medium impact |
| 0.5 | Low impact |
| 0.25 | Minimal impact |

**Confidence** — how sure you are about the Reach/Impact estimates, not
about whether the fix works:

| Confidence | Meaning |
|---|---|
| 100% | High — measured or unambiguous from the item's own text |
| 80% | Medium — a reasonable inference, some assumption involved |
| 50% | Low — the item's own text flags the scope/root-cause as unresolved |

Never score above 80% confidence unless something was actually measured
(a metric, a test count, a confirmed production query) — a single reviewing
pass reading prose is not grounds for 100% on anything but the most
trivially-scoped item.

**Effort** — person-weeks of engineering (or the equivalent business/GTM
effort — see caveat below). Use the smallest unit that distinguishes items:
0.5 (a config toggle or one-line fix), 1, 1.5, 2, 3, 5, 8+ (a multi-week
epic).

**Score** = (Reach × Impact × Confidence) / Effort, confidence as a decimal
(80% → 0.8).

## Methodology notes to fill in for this pass

- **Item count**: state how many candidate items you actually found vs. how
  many were assumed going in. Backlog files drift — expect the real count
  to differ from any prior estimate.
- **Exclusions**: name what you filtered out and why (closed items,
  items the source file itself marks superseded/historical, duplicates).
  Don't silently drop something that looked closed but wasn't — say what
  the filter rule was.
- **Splits**: if one heading bundles multiple independently-shippable
  sub-tasks, split them into separate scoreable rows (e.g. `C42-C`,
  `C42-D`) rather than forcing one score onto a bundle with different
  effort/impact per piece.

## Sorted by RICE score (descending)

| Rank | Item | Reach | Impact | Confidence | Effort | Score | One-line why |
|---|---|---|---|---|---|---|---|
| | | | | | | | |

## Read before acting on this ranking

- Call out any item whose rank is driven almost entirely by one extreme
  input (e.g. near-zero effort) — worth a sanity check that the claimed
  effort is real before treating it as the top pick.
- Call out any item where Confidence is capped below 80% because its own
  text says the scope or root cause is unresolved — RICE from a single
  read is weakest exactly here; flag it as needing a scoping pass, not
  just a lower number.
- Note where Reach for backend/infra-only items is an ordinal judgment
  call, not a measured user count.
- Note any item whose Effort is business/GTM work rather than engineering
  — its score isn't directly comparable to the technical items around it.
- List what was excluded (see Methodology notes) so a reader can tell
  deliberate exclusion from an oversight.

## Recommendation

State a short, concrete "do these next" slate — which top-ranked items
cluster together (e.g. several unblock the same downstream item, or share
near-zero effort), and flag the one or two highest-scored items that are
genuinely uncertain (low confidence) and need a scoping conversation before
committing engineering time, rather than being treated as ready to pick up.
