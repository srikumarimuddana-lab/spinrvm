# Escalation: Does Saskatchewan PST apply to rideshare fares? (Regina data mismatch)

**Status:** Open — blocked on an authoritative answer. Nothing in this doc should
be treated as resolving the question either way.
**Owner:** whoever picks this up with real research/legal access (see "Who should
answer this" below) — not resolvable from an engineering session alone.
**Tracked as:** `ACTION_ITEMS.md` B26
**Related:** `docs/change-log/2026-08-11-sk-pst-enable.md`,
`docs/change-log/2026-08-12-a29-tax-config-audit-justification.md`,
`.claude/context/regulatory-sk.md`

---

## The one question this needs answered

**Does Saskatchewan's Provincial Sales Tax (PST, 6%) apply to rideshare /
passenger-transportation-for-hire fares in Saskatchewan?**

Everything below exists to get that answered by someone who can actually verify
it against a primary source, and to make the fix mechanical once it is.

---

## Why this is urgent: live data disagrees with itself

Spinr's `service_areas` table has 4 real Saskatchewan rows. As of 2026-08-12:

| Area | `pst_enabled` | `pst_rate` |
|---|---|---|
| Saskatoon | `true` | 6 |
| Saskatoon Airport | `true` | 6 |
| Regina Airport | `true` | 6 |
| **Regina** (main, non-airport) | **`false`** | 6 |

Three of four rows charge PST on every fare; the fourth — Regina, the city
itself, one of Spinr's two primary markets — does not, despite having the same
`pst_rate=6` sitting unused. This is not a config that could have been set this
way on purpose by two different deliberate actions; it's either:

- **A live bug**: if PST applies to Saskatchewan rideshare, every Regina ride
  since whenever this flag went false has **under-collected PST by 6% of the
  taxable fare** — a real, ongoing CRA/SK remittance shortfall, not a
  hypothetical. This directly re-opens the exact gap the 2026-08-11 change was
  supposed to close.
- **An intentional, undocumented reversal**: someone may have found a real
  reason PST shouldn't apply in Regina specifically (or at all) and turned it
  off without leaving a record. Also possible, and not something to overwrite
  without checking first.

Either way, the current state is `service_areas` disagreeing with itself, which
is worse than either alternative on its own.

## Timeline (how this got here)

1. **Before 2026-08-11**: all 4 Saskatchewan rows had `pst_enabled=false`.
   `backend/features.py`'s `calculate_all_fees` carried a code comment stating
   *"Saskatchewan rideshare is GST 5% only — PST does NOT apply to rideshare
   here."*
2. **2026-08-11**: a session investigating an unrelated audit finding surfaced
   this and asked the user directly. **The user confirmed PST does apply** to
   Saskatchewan rideshare and the code/comment was wrong. All 4 rows were
   updated to `pst_enabled=true, pst_rate=6` via direct Supabase `UPDATE`,
   verified at the time by a `RETURNING`-clause query showing exactly 4 rows
   changed, all correctly named (full record:
   `docs/change-log/2026-08-11-sk-pst-enable.md`).
3. **2026-08-12**: a different session, working on an unrelated task (a Regina
   service-area rename), queried `service_areas` as part of a routine
   blast-radius check and found `Regina.pst_enabled=false` — despite the
   2026-08-11 log's explicit claim that all 4 rows were changed and verified.
   Re-asked the user the same question. **This time the answer was**: *"PST is
   not applicable for Saskatchewan we might want to verify by researching"* —
   which does not confirm the 2026-08-11 determination and reads as
   materially less certain than it.
4. That session attempted to verify against a primary source
   (Saskatchewan's PST-46 bulletin, "Service Enterprises" — the bulletin most
   likely to state whether passenger-transportation-for-hire is an enumerated
   taxable service) and **could not**: `WebFetch` returned `EGRESS_BLOCKED` for
   every domain tried (`sets.saskatchewan.ca`, `www.saskatchewan.ca`,
   `canada.ca`, third-party tax-law summaries). `WebSearch`'s AI-summarized
   snippets leaned toward *GST-only, PST not applicable to ride-sharing
   specifically* — but these are second-hand summaries of search results, not
   verified primary-source text, and were explicitly not treated as resolving
   anything.

**Net effect:** two independent signals (the user's own second answer, and an
unverified web search) now cast doubt on the 2026-08-11 determination, without
actually overturning it. Nobody has read the actual bulletin text in this
process yet.

## What actually needs to happen

1. **Get a real answer from a source that can be trusted for a live
   tax-compliance decision.** Any of:
   - An accountant or tax professional with SK PST experience.
   - Direct contact with the Saskatchewan Ministry of Finance, Revenue
     Division (the body that administers PST).
   - Someone/some session with working, unblocked web access reading the
     actual current text of PST-46 ("Service Enterprises") or whichever
     bulletin governs passenger-transportation-for-hire — not a search-engine
     summary of it.
   - If Spinr has outside counsel or a tax advisor on retainer, this is
     exactly the kind of question to route to them rather than resolve
     in-house from secondary sources.
2. **The answer decides all 4 rows, not just Regina.** This is not "make
   Regina match the other three" — Saskatoon's, Saskatoon Airport's, and
   Regina Airport's current `true` values now carry the same doubt Regina's
   `false` does, since they all trace back to the same 2026-08-11
   determination that's now in question. Get the policy right first, then
   make all 4 rows match it.
3. **Decide whether prior rides need remediation.** If the determination
   confirms PST applies and Regina under-collected it for some window, or if
   the determination reverses course and Saskatoon/Regina-Airport
   over-collected it — decide whether affected rides need a refund/credit, or
   whether (per the 2026-08-11 log's own "no backdating" precedent) only
   fares quoted from the fix forward are affected. This is a business/finance
   call, not something to default silently either way.
4. **Apply the fix through the audited path, not a bare `UPDATE`.** As of
   2026-08-12, `admin_update_service_area` (`routes/admin/service_areas.py`)
   requires a written `tax_justification` and writes a `tax_config_updated`
   audit-log entry for any GST/PST/HST field change (see
   `docs/change-log/2026-08-12-a29-tax-config-audit-justification.md`). Use
   that admin-dashboard flow (or an equivalent direct-SQL change accompanied
   by the same justification + audit trail) rather than a silent update —
   the org has now explicitly recognized tax-config changes as needing that
   discipline, and this determination is exactly the kind of change it exists
   to cover.
5. **Write a Change Impact Log either way.** Whichever way this resolves is a
   "silent behavior change to a live-tested flow" per root `CLAUDE.md`'s
   pre-merge gate rules — document the final determination, the source it
   came from, and the exact rows changed, the same way the 2026-08-11 log did.
   This is also the record that stops a fourth flip-flop: right now there is
   no single place that says "here is the actual, current, confirmed policy
   and here is who confirmed it."

## Who should answer this

Not an engineering session — twice now, engineering sessions have tried to
resolve this by asking the person available in the moment and gotten two
different answers, and a third attempt to verify independently was blocked
before it could read a primary source. This needs one of:

- A named accountant/tax advisor, or
- SK Ministry of Finance Revenue Division direct contact, or
- Whoever at Spinr owns tax/regulatory compliance decisions, working from the
  actual bulletin text (not a summary of it)

— and their answer, once obtained, should be the one written into the Change
Impact Log this item requires, so it stops drifting.

## What NOT to do

- Do not flip `Regina.pst_enabled` to match the other three "to be
  consistent" without resolving the underlying question — that risks making
  the wrong 4 rows agree with each other instead of the right ones.
- Do not treat the 2026-08-12 session's web-search snippets as a
  determination — they were explicitly logged as inconclusive, not as an
  answer.
- Do not apply any fix via a bare `UPDATE` outside the audited admin path
  now that one exists (see step 4 above).
