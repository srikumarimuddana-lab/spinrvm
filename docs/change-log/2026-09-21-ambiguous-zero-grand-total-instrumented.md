# Change Impact & Risk Log — measure the ambiguous `grand_total = 0` before fixing it

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code session, on the repository owner's request |
| Surface(s) | backend |
| Domain (Sentry tag) | payments |
| PR / commit link | branch `claude/loving-thompson-amjk8b` |
| Related issue or gap ID | 2026-09-20 review, falsy-zero `grand_total` coalesce |

## 1. Issue / gap identified

Three money call sites resolved a ride's total as
`ride.get("grand_total") or ride.get("total_fare") or 0`. `or` means **"if falsy"**, not
**"if missing"**, and those diverge at exactly one value — `grand_total = 0` — which carries two
incompatible meanings:

- a **genuinely free ride** (100% promo, in an area with no fees and no GST), or
- a **row predating migration 46**, which added the column as `DECIMAL(10,2) DEFAULT 0` rather than
  NULL — so "never computed" also reads as `0`.

Both are falsy, so both fall back to `total_fare`, the **pre-tax subtotal**. Correct for the legacy
row; an overcharge for the free ride.

The three sites are not equally serious, which matters for what to do about it:

| Site | What the value is used for | Severity |
|---|---|---|
| `_ride_total_with_fallback(..., site="guest_corporate_auto_settle")` (was `:1529`) | passed as `total_charge` to `settle_corporate` | **money actually charged** |
| `site="refund_tax_reversal"` (was `:326`) | sets `frac`, which computes `tax_reversed` — the GST/PST booked to the **remittance ledger** on a partial refund | **a tax figure, not a display value** |
| `site="wallet_transaction_record"` (was `:939`) | written into a `wallet_transactions` row **after** the RPC moved money | recorded value only |

**Correction (post-review).** An earlier draft of this table called the
`refund_tax_reversal` site a "recorded value; already guarded for zero" and ranked it with the
wallet-record site. That understated it, and `spinr-money-auditor` caught it: the value sets the
fraction used to compute the reversed GST/PST that is booked to the remittance ledger, so if the
ambiguous case fires there the tax reversal is computed against the pre-tax subtotal instead of the
true grand total — a wrong number in a tax record, not a cosmetic one. Behaviour there is
pre-existing and unchanged by this diff, but it ranks **second** for the eventual fix, not third.

## 2. Root cause

`or` was used as a null-coalesce. That is a correct idiom when the fallback value can never be a
meaningful zero — and whoever wrote it was very likely reasoning about the legacy-row case, where
the fallback is genuinely right. The defect is that the same expression silently took on a second
job when free rides became possible.

## 3. Fix / remediation — deliberately *not* a behaviour fix

One shared helper, `_ride_total_with_fallback(ride, *, site)`, now backs all three sites. It
**returns exactly what the old expression returned**, and additionally logs and increments
`spinr_payment_zero_grand_total_fallback_total{site}` when `grand_total` is present-but-falsy while
`total_fare` disagrees.

**Why measure instead of fix.** Resolving the ambiguity wrongly costs real money in *both*
directions:

- Treat 0 as "free" → every legacy row settles at **$0**. Silent revenue loss, and under the
  standing policy the driver is still paid, so Spinr absorbs it
  (`docs/change-log/2026-09-21-uncollected-rides-stay-payable.md`).
- Treat 0 as "not computed" (today) → a genuinely free ride is **charged the pre-tax subtotal**.

Nobody on this change can query production to learn which rows exist, so guessing would be a coin
flip on a money path. This preserves today's arithmetic exactly and produces the data to decide
with. Escalated to and chosen by the repository owner, per CLAUDE.md pre-merge gate 9.

**The log records the raw value's Python type on purpose.** If PostgREST returns this `numeric`
column as a **string**, `"0.00"` is truthy, it is used as-is, and the ambiguity cannot arise at all
— the bug would be theoretical. One production log line settles that; proving it from here would
not.

**Alternatives considered:** (a) fix the charge site now — rejected, it cannot be made airtight
against the legacy case without the data; (b) hand-write read-only production SQL for the owner to
run — viable and offered, but slower and this instrumentation answers the same question
continuously and in situ.

## 4. Risk & impact on existing functionality

**Zero behaviour change, and this was proven rather than asserted.** The three original spellings
differed slightly — `:939` used `.get("total_fare", 0)` and `:1529` wrapped the result in `str()`
before `_d`. All three were executed against the helper's expression over ten input shapes
(string/numeric/None/absent/zero/negative/empty). **All three agree with the helper on every
shape**, and the ambiguity flag fires on exactly one of them. The two spelling variants are no-ops:
`.get(k, 0)` and `.get(k)` both yield `0` after `or 0`, and `_d(str(x)) == _d(x)` because `_d` is
`Decimal(str(v))`.

**Blast radius:** the helper is new, with three callers, all replaced in this diff. Grepped for
other uses of the old expression — none remain. The counter is new, so no dashboard or alert can
break. No other function, table, or code path is touched.

**Not a Sentry event.** The log is `warning`, below `server.py`'s `event_level="ERROR"` threshold,
deliberately: this is a measurement, not a failure, and nobody should be paged. The **counter** is
the signal to watch.

**PII:** logs `ride_id` (an ID) and two money amounts. No name, phone, email, or location.

**No migration, no schema, no settings row, no feature flag, no API change.**

## 5. User-experience effect

**None, for anyone.** Every rider, driver and corporate account is charged exactly what they were
charged before this commit — that is the entire point. Visible only in logs and `/metrics`.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/services/payment_service.py` | new `_ride_total_with_fallback` + `_ride_total_metric_inc`; 3 call sites routed through it | measure the ambiguity |
| `backend/tests/test_ride_total_ambiguous_zero.py` | new — 15 tests | pin value-equivalence and which cases count |
| `docs/change-log/2026-09-21-ambiguous-zero-grand-total-instrumented.md` | this file | |

## 7. Before / after

**Before** — three near-identical expressions, the ambiguity invisible:

```python
total = _round(_d(str(ride.get("grand_total") or ride.get("total_fare") or 0)))
result = await settle_corporate(ride, ride_id, total_charge=total, tip_amount=Decimal("0"))
```

**After** — same number, now countable:

```python
total = _ride_total_with_fallback(ride, site="guest_corporate_auto_settle")
result = await settle_corporate(ride, ride_id, total_charge=total, tip_amount=Decimal("0"))
```

**Concrete scenario.** A guest-corporate ride settles with `grand_total = 0` and
`total_fare = 40.00`. *Before and after:* the corporate account is charged **$40.00** — unchanged.
*New:* a warning naming the site and the raw type, and
`spinr_payment_zero_grand_total_fallback_total{site="guest_corporate_auto_settle"}` increments. If
that counter stays flat, the bug is not reachable in production and can be closed as theoretical.
If it moves, the log says whether those are free rides or legacy rows, and the fix follows.

## 8. Rollback plan

`git revert` + redeploy. Nothing is persisted, migrated, or written to any table, and the returned
value is unchanged, so a revert is a pure no-op on behaviour — the log and counter simply stop.
Nothing consumes the counter yet.

## 9. Verification performed

- **Executed** the three original expressions and the helper's expression side by side over ten
  input shapes and confirmed all agree on every one (output in the commit message). This is the
  central claim of the change, so it was run rather than reasoned.
- Confirmed `_d(v) = Decimal(str(v)) if v is not None else Decimal("0")` by reading it, which is
  what makes `:1529`'s `str()` wrapper a no-op.
- Confirmed `discount = min(discount_value, ride_fare)` (`routes/promotions.py:305`) caps the
  discount at the fare — so a genuinely-$0 `grand_total` additionally needs zero fees and zero tax,
  which is what makes it narrow rather than impossible.
- Read all three call sites in full to classify which is a charge and which is a record; only one
  is a charge, and that distinction drove the decision not to blanket-"fix" them.
- Confirmed the helper is defined above its first use, and that loguru placeholder count (5)
  matches the positional args (5), with no `%s`, `exc_info=` or `extra=`.
- `ruff check`, `ruff format --check`, `py_compile` clean.

## 10. What was NOT verified — read this before merging

- **No test was executed.** `pytest` is not installed here and PyPI is blocked by policy, so CI runs
  them first. (The *equivalence* claim was separately proven by running the expressions directly in
  a plain interpreter — see §9 — which is the part that most needed it.)
- **Whether the ambiguous case happens at all in production is unknown — that is the open question
  this commit exists to answer, not one it answers.** Do not read this as "the bug is fixed"; the
  overcharge described in §1 is still live and unchanged, by design.
- Whether PostgREST returns this `numeric` column as a string or a number was **not** determined.
  If it is a string, the ambiguity is unreachable and this instrumentation will simply stay flat.
- Not exercised against a real Supabase, a real free ride, or a real legacy row.
- The counter has no alert or dashboard attached. Someone needs to actually look at it, or this
  commit achieves nothing.
- Backend-only: no `admin-dashboard` build, none of the 6 merge-blocking visual baselines touched.
