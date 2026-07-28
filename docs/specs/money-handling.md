# Money-Handling Spec (draft — for review, no code changes yet)

Source: `docs/audit/findings.md` items 2 and 5 (three parallel Decimal helper
definitions; float creeping into mid-calculation money code). Scope is
money arithmetic only — ride-status code is out of scope for this pass.

## 1. `backend/utils/money.py` — existing contract (treated as source of truth)

`money.py` is a small, self-contained module with three functions. It appears to
have **zero current importers** in `fare_service.py`, `payment_service.py`, or
`routes/rides/_shared.py` — each of those files reimplements an equivalent
helper locally instead of calling it.

| Function | Input | Output | Rounding |
|---|---|---|---|
| `to_decimal(amount: Money)` | `int \| float \| str \| Decimal` | `Decimal`, quantized to 2dp | `ROUND_HALF_UP` |
| `dollars_to_cents(amount: Money)` | same | `int` cents | `ROUND_HALF_UP`, via a quantize-then-scale-then-quantize path |
| `cents_to_dollars(cents: int)` | `int` | `Decimal`, quantized to 2dp | `ROUND_HALF_UP` |

Key contract points, from the module docstring and implementation:

- **All numeric coercion goes through `Decimal(str(x))`**, never `Decimal(x)` on
  a float directly — this is deliberate: `Decimal(str(0.1))` → `Decimal('0.1')`
  (clean), whereas `Decimal(0.1)` → `Decimal('0.100000000000000005551115...')`
  (binary-float artifact). This is the load-bearing invariant the whole module
  exists to protect (docstring cites a real prior bug: `int(29.99*100)` → 2998
  cents instead of 2999, undercharging a rider by a cent).
- **Currency is implicitly CAD, 2 decimal places, no multi-currency handling.**
  There's no currency-code parameter anywhere — this matches the rest of the
  codebase (Spinr is Saskatchewan-only), so not treated as a gap.
- **`float` is documented as a serialization-boundary-only type.**
  `cents_to_dollars`'s docstring says explicitly: "Returns Decimal so callers
  can keep arithmetic exact. If a float is needed at a serialization boundary,
  the caller converts explicitly." `money.py` itself never returns a float.
- Money.py has no equivalent of a "convert Decimal back to float for JSON" helper
  — that conversion is left to callers, which is exactly where the three
  duplicated helpers below diverge from each other.

This module's contract is sound and is the one to converge on. The concrete gap
is that a fourth "Decimal → float, for the API response" step isn't part of its
public surface, so every call site invented its own.

## 2. Where the three local helpers differ — from each other and from `money.py`

### `_d()` — string/numeric → Decimal

| Site | Definition | Behavior vs. `money.py.to_decimal` |
|---|---|---|
| `money.py.to_decimal` | `Decimal(str(v)).quantize(0.01, HALF_UP)` | — |
| `fare_service.py:41-42` | `Decimal(str(v))` — **no quantize** | Returns unrounded Decimal (can carry >2dp, e.g. from a division at `_shared.py:357-358` / `fare_service.py:345`) |
| `payment_service.py:39-40` | `Decimal(str(v)) if v is not None else Decimal("0")` — **no quantize**, adds a `None`→`0` guard the others lack | Same rounding gap as above, plus a null-safety behavior the other two don't have (silently masks missing data as `$0` rather than raising) |
| `routes/rides/_shared.py:324-326` | `Decimal(str(v))` — **no quantize** | Same rounding gap as fare_service's |

None of the three `_d()` implementations quantize on the way in — they only
round at the `_round()` step, which callers must remember to call before every
persist/display. `money.py.to_decimal` quantizes unconditionally. This means a
raw `_d()` result can silently carry 3+ decimal places through several lines of
arithmetic (e.g. `per_km_effective = _d(...) / (old_dist * surge)` at
`_shared.py:357`) before anyone rounds it — the correctness depends entirely on
every call site remembering to wrap in `_round()`, which is not enforced.

### `_round()` — Decimal → 2dp Decimal

All three local definitions and `money.py.to_decimal`'s internal quantize step
use the same `Decimal("0.01")` / `ROUND_HALF_UP` — **this part is actually
consistent** across all four locations. Not a divergence in rounding *mode*,
only in *when* it's applied (see above).

### `_f()` — Decimal → float (the "serialization boundary" step `money.py` doesn't define)

This is where the real behavioral divergence is:

| Site | Definition | Behavior |
|---|---|---|
| `fare_service.py:50-52` | `return float(v)` | Trusts the caller already rounded `v`. If a caller passes an un-rounded Decimal, the float carries whatever precision `v` had. |
| `payment_service.py:47-48` | `return float(_round(_d(v)))` | Re-derives and re-rounds from scratch every call — safe regardless of caller discipline, but silently masks a caller bug that passed something already wrong. |
| `routes/rides/_shared.py:407,417-419` | `return float(v)` (same shape as fare_service's) | Same trust-the-caller behavior as fare_service.py. |

So `payment_service._f` is defensive (always re-rounds), while `fare_service._f`
and `_shared._f` are not — two different reliability postures for a
correctness-critical conversion, with no test asserting either behavior.

### Raw `float()` calls bypassing all of the above (the item-5 finding)

Two spots do direct `float(...)` arithmetic on money-typed fields, outside any
of the three helpers, which is exactly the class of bug the pre-commit
float-arithmetic hook exists to catch:

- **`routes/rides/_shared.py:599-609`** (promo-discount capping in the fare
  breakdown): `raw_discount = float(ride["discount_amount"])`,
  `ride_fare = float(base + dist_surged + time_surged + uplift)` — mixes
  already-Decimal values (`base`, `dist_surged`, `time_surged`, `uplift`) by
  casting the whole sum to float, then does a `min()` comparison and
  subtraction in float space, before storing `-capped_discount` directly into a
  fare-breakdown line the rider sees on their receipt.
- **`routes/rides/queries.py:560-577`** (driver earnings-summary tax
  fallback): when `ride.tax_amount` is `0`, the code re-derives tax by summing
  `float(ln.get("amount") or 0)` over fare-breakdown line items in a running
  float accumulator (`tax += float(...)`), then `round(tax, 2)` once at the
  end — classic float-accumulation drift risk if the loop has more than a
  couple of tax lines (GST + PST + area fees), and it's on a driver-facing
  earnings total, not just a display string.

Both are receipt/earnings-facing, not just internal bookkeeping, which is why
these are flagged medium rather than low.

## 3. Proposed single helper interface

Converge all money-adjacent modules onto `backend/utils/money.py`, extended
with the one function it's currently missing (the Decimal→float boundary
step), rather than inventing a fourth new module:

```python
# backend/utils/money.py — additions only, existing 3 functions unchanged

def to_float(amount: Money) -> float:
    """Convert to a float for the API/JSON boundary only.

    Always re-quantizes through to_decimal() first — callers never need to
    remember to round before calling this. This is the *only* sanctioned
    place a money value becomes a float in the codebase; every other money
    call site keeps Decimal until this boundary.
    """
    return float(to_decimal(amount))
```

Call-site migration plan (naming, not behavior, since `to_decimal`'s rounding
already matches all three existing `_round()`s):

- `fare_service._d` / `payment_service._d` / `_shared._d` → `money.to_decimal`
  (this changes behavior slightly: it now quantizes on the way in, closing the
  "un-rounded Decimal drifting through arithmetic" gap above — flagged
  explicitly since it's the one place this spec proposes a behavior change,
  not just a rename)
- `fare_service._round` / `payment_service._round` / `_shared._round` → drop;
  callers use `money.to_decimal` directly, or a thin `money.round_decimal`
  alias if call sites need to round an already-Decimal intermediate without
  re-parsing a string
- `fare_service._f` / `payment_service._f` / `_shared._f` → `money.to_float`
  (adopts `payment_service`'s defensive always-re-round behavior everywhere,
  since that's the safer of the two existing postures)
- `queries.py`'s manual `tax +=` float accumulator → sum in `Decimal` (`_d()`/
  `money.to_decimal` per line item, `sum(..., Decimal("0"))`), convert once via
  `money.to_float` at the end
- `_shared.py`'s promo-discount capping → keep `base`/`dist_surged`/
  `time_surged`/`uplift`/`raw_discount` as `Decimal` through the `min()` and
  subtraction, convert to float only for the final `lines.append(...)` amount

Open question for the follow-up implementation task: whether `_d`'s `None`→
`Decimal("0")` guard (currently only in `payment_service._d`) should become
part of the shared `to_decimal`, or stay a call-site concern — recommend
folding it in as `to_decimal(amount, default=Decimal("0"))` with `default=None`
raising by default, so payment code keeps its null-safety without silently
extending that behavior to fare code paths that may want to fail loud on a
missing value instead.

No code has been changed. Awaiting go-ahead before implementing.
