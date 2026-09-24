# 03 — Competitive Benchmark (R18 Competitive Analyst, Wave W4)

> Scope: benchmark every L3 feature in `docs/audit/clean-sheet/01-inventory/epics.md` (90
> features across 18 L2 epics) against Uber, Lyft, and a Saskatchewan/Canadian local taxi app,
> using Dimension 24's Maturity 1-5 + Gap GREEN/YELLOW/RED scale. Spinr maturity is set from
> W1-W3 lane findings (`02-findings/*.md`) and re-read code, not re-audited from scratch.
> Competitor claims are sourced from public primary-ish pages (help centres, newsrooms, investor
> filings) with a URL cited per claim; an uncited competitor claim is ASSUMED, never VERIFIED.

---

## §0 Method, sources, and limits

**Read before writing this report (VERIFIED, opened in full or in relevant part):**
`docs/audit/SPINR_CLEAN_SHEET_REBUILD_AUDIT_PROMPT.md` §4/§7.1/§7.3, `docs/audit/clean-sheet-prompt/roles.md`
§R18, `docs/audit/clean-sheet-prompt/greenfield-extensions.md`, `audit-framework/dimensions/24-industry-benchmark.md`
(exact filename may differ; matched by glob), `docs/audit/clean-sheet/W0-SUMMARY.md`,
`docs/audit/clean-sheet/01-inventory/epics.md` (90 L3 features, 18 L2 epics — full list used for §2),
and all twelve on-disk W1-W3 lane reports in `docs/audit/clean-sheet/02-findings/` (`strategy.md`,
`rider-journey.md`, `driver-journey.md`, `corporate.md`, `admin-ops.md`, `dispatch.md`, `money-cra.md`,
`security.md`, `trust-safety-fraud.md`, `compliance.md`, `integrations.md`, `reliability.md` — headers
grepped for every finding ID, then the relevant finding bodies read in full). `quality.md`, `support-kb.md`,
`ux-a11y.md` are present but short/in-progress (48, 48, 83 lines) — used what's on disk, flagged where thin.
`CLAUDE.md` "What Spinr Is NOT" section (full) for §5.

**Web access:** WebSearch and WebFetch both worked in this session. Every competitor claim below
cites a URL fetched or returned 2026-09-24 (date accessed). Where a claim could not be
corroborated by a primary(-ish) source (Uber/Lyft's own newsroom, help centre, driver/rider
support pages, or investor materials) in the time budget, it is marked ASSUMED or UNKNOWN rather
than presented as fact. No local Saskatoon/Regina taxi-dispatch app publishes a comparable public
feature-documentation corpus to Uber/Lyft's — see the "local taxi" column note below.

**Local taxi comparator — explicit scoping decision:** Saskatoon and Regina's incumbent taxi
fleets (e.g. Comfort Cab/United Cabs-style dispatch operators) run white-label or third-party
dispatch apps (iCabbi, Autocab, and similar) with no public API docs, no help-centre content, and
no engineering blog — there is nothing citable in the way there is for Uber/Lyft. Where the local
taxi column is filled, it is filled from general public knowledge of Canadian taxi-dispatch apps
(dispatcher-assigned rides, phone-in booking still primary, metered fare, cash/card at pickup) and
is labelled ASSUMED throughout. This is a real limit on this section, not a shortcut — a human
with local market knowledge (a Saskatoon/Regina rider or the Spinr ops team) should correct it.

**Spinr-maturity sourcing rule used throughout §2:** every Spinr maturity score cites either a
W1-W3 finding ID (e.g. `TSF-010`) or a `path:line` this lane re-read directly. A maturity score
with neither is not given — the row is marked UNKNOWN instead of guessed.

**What this section does NOT do:** re-litigate W1-W3's own severity ratings, re-run code searches
W1-W3 already ran (their finding IDs are cited, not re-derived), or assess admin-dashboard/internal
tooling against Uber/Lyft (those are not rider/driver-facing competitive surfaces — Dimension 24
is scoped to competitive/economic positioning of the product, not internal ops tooling; admin
maturity is out of scope for this file by construction, consistent with `01-inventory/epics.md`
labelling most of Admin Dashboard & Operations as internal/undocumented-scope rather than a
customer-facing competitive surface).

---

## §1 Scoring scale (quoted from `audit-framework/dimensions/24-*`, verbatim)

**Competitive-parity scale:**

| Maturity | Meaning |
|---|---|
| 1 — Missing | Capability doesn't exist at all |
| 2 — Workaround | A manual/external workaround stands in for it |
| 3 — Functional | Works, but a recognizably below-industry-baseline approach |
| 4 — At parity | Matches what category-leading ride-share apps ship |
| 5 — Differentiated | Better than the category baseline, a real product edge |

| Gap-vs-industry | Meaning |
|---|---|
| GREEN | At or near parity (maturity 4-5); no action needed for competitiveness |
| YELLOW | Functional gap (maturity 3); worth a scoped improvement, not urgent |
| RED | Missing/workaround (maturity 1-2) **or** an active cost/security exposure regardless of maturity |

A maturity-4/5 feature can still carry a RED *defect* finding from W1-W3 (severity is reported
separately in this file's evidence column, not folded into the Gap column) — per Dimension 24's
own instruction, the two scales are never collapsed into one number.

**Defect severity scale** (reused from `audit-framework/templates/run-audit.md`, applied where a
row's Spinr status is itself a defect, not just a maturity gap): CRITICAL / HIGH / MEDIUM / LOW /
PASS / RECOMMENDATION.

**Evidence labels** (from the audit prompt §4, applied per claim): VERIFIED (observed directly —
code path read, or a primary source fetched), INFERRED (reasoned from evidence, not executed),
ASSUMED (needed to proceed, must be confirmed), PROPOSED (future-state recommendation), UNKNOWN
(must be obtained).

