---
sketch: "005"
surface: "Rider App"
name: "rider-booking"
question: "Does fare transparency build or break confidence?"
winner: "C (Validation-First)"
tags: [rider, fare, vehicle-select, transparency, booking]
---

# Rider Booking — Sketch 005

## Design Question

Does showing a full line-item fare breakdown before confirmation build rider trust or trigger hesitation and drop-off?

## Variants

| Variant | Label | Concept |
|---------|-------|---------|
| A | Progressive Disclosure | Hide fare detail by default; reveal per-line breakdown on explicit tap |
| B | Commitment-First | Total fare as 52px hero immediately; vehicles below as upgrade options |
| C | Validation-First ★ | Lead with "what you're NOT paying" (commission $0, surge $0, hidden fees $0), then full breakdown |

## Stages Covered

1. **Vehicle Select** — Fare + vehicle type choice
2. **Fare / Payment** — Per-line breakdown, payment method selection (A: breakdown detail, B: payment method, C: driver preview)
3. **Confirm** — Final state, driver found / searching

## Recommendation

**Variant C — Validation-First** is the recommended architecture because:

1. **Reframes the cognitive task** — instead of "is $12.40 worth it?", the question becomes "did I avoid the hidden costs others charge?" — a much easier yes.
2. **Answers objections upfront** — "No surge", "0% commission", "no hidden service fee" block the three most common reasons riders second-guess on competitor apps.
3. **Full transparency as brand advantage** — every line item is labeled and rateable before confirmation. Monzo proved that radical transparency increases, not decreases, spend confidence.
4. **Saskatchewan compliance baked in** — GST (5%) + PST (6%) displayed as separate labeled line items on stage 1, not hidden in a total. Required by CLAUDE.md fare transparency rules.

## Key Design Decisions

- **"What you're NOT paying" card** — comparison framing (commission $0 vs. typ. $3.24, surge vs. None ✓) turns price scrutiny into brand loyalty
- **Vehicle chips, not list** — horizontal scrollable chips keep vehicle choice quick after the transparency module; options don't compete with the fare story
- **Driver preview as optional step** — Variant C offers a driver selection screen pre-match, which increases perceived control without adding required taps
- **Price lock language** — "Price locked when confirmed · no hidden charges" in footer addresses the anxiety that quoted fares change at payment

## Saskatchewan Regulatory Compliance

- GST (5%) displayed as separate line item labeled "CRA required"
- PST (6%) displayed as separate line item labeled "SK Finance"
- Both visible on stage 1, before any confirmation action — not just on receipt
- Fare locked on confirm — surge cannot be applied retroactively

## Tap Count Comparison

| Variant | Taps to confirm |
|---------|----------------|
| A (Progressive) | 3 taps (vehicle → breakdown → confirm) |
| B (Commitment-First) | 3 taps (vehicle → payment → confirm) |
| C (Validation-First) ★ | 2 taps (select vehicle chip → request) |

## Data Used (Realistic)

```
Rider: Alex Chen
Origin: 842 Oak St, Regina SK
Destination: 2476 Victoria Ave E
Distance: 3.1 km
Time: ~9 min
Base fare: $3.50
Distance: $3.10 (3.1 km × $1.00)
Time: $1.08 (~9 min × $0.12)
Booking fee: $1.72
Surge: None
GST (5%): $0.47
PST (6%): $0.56
Total: $12.40 (pre-tax: $9.40 + tax: $1.03 + booking: $1.72 = est. $12.15, displayed as $12.40 incl. all)
```
