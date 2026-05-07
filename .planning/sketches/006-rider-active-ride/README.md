---
sketch: "006"
surface: "Rider App"
name: "rider-active-ride"
question: "Right balance of info vs calm during a ride?"
winner: "C (State-Aware)"
tags: [rider, active, tracking, safety, receipt]
---

# Rider Active Ride — Sketch 006

## Design Question

What information density is right at each phase of an active ride — driver en route, in trip, and arrival — without causing anxiety or hiding safety features?

## Variants

| Variant | Label | Concept |
|---------|-------|---------|
| A | Information-Dense | Full data at all times: driver route steps, running fare meter, turn-by-turn nav strip |
| B | Calm Minimal | Near-empty map with a single float card; radical information reduction |
| C | State-Aware Narrative ★ | Info density shifts per state: high during wait, ambient during trip, celebratory at arrival |

## Stages Covered

1. **Driver En Route** — wait state; driver heading to pickup
2. **In Trip** — passenger aboard; en route to destination
3. **Trip Complete** — arrived; receipt + rating

## Recommendation

**Variant C — State-Aware Narrative** wins because it models the rider's actual anxiety curve:

- **Wait state is high anxiety**: "Where is my driver?" is best answered with a human narrative ("Alex is 2 stops away, heading south on Oak St now") not just a timer. Driver identity, safety strip, and contact options all surface here.
- **In-trip is low anxiety**: Rider is in the car; the driver drives. Running fare in corner, ETA strip, and progress bar are enough. Clearing the screen reduces distraction and signals trust.
- **Arrival is celebratory + functional**: Monzo-style receipt with every line item, "Commission to Spinr: $0.00 ✓", and a rating framed as "helps Alex grow their business" — brand reinforcement at the emotional high point.

## Key Design Decisions

- **Narrative card over timer** — "Alex is 2 stops away" vs. "3:00 remaining". Human framing reduces abstract anxiety.
- **Safety strip as trust signal, not alert** — "Trip protected. Your location is shared with emergency contacts." — passive assurance during the most anxiety-prone state.
- **SOS always in view, never buried** — pill on in-trip state, chip on safety strip during wait. Every state has SOS reachable in 1 tap.
- **Running fare as ambient, not hero** — small gold float in top-right during trip. Not hidden, not dominant.
- **Receipt = brand moment** — "Your driver kept 100%" on every completed trip receipt reinforces the 0% commission model at the exact moment of emotional satisfaction.

## Saskatchewan Regulatory Compliance

- GST (5%) labeled "Canada Revenue Agency" on receipt
- PST (6%) labeled "SK Finance" on receipt
- Both displayed as separate line items — not bundled into total
- "Commission to Spinr: $0.00 ✓" visible on receipt for driver transparency

## Safety Coverage (per CLAUDE.md principle: SOS within 2 taps)

| State | SOS access |
|-------|-----------|
| Driver en route | Safety strip chip — 1 tap |
| In trip | Inline pill (top left) — 1 tap |
| Trip complete | Not needed — trip ended |

## Data Used (Realistic)

```
Rider: Alex Chen
Driver: Alex Johnson ★4.9 · 1,240 trips
Vehicle: Toyota Camry 2022 · Plate GXY 847-KRX
Route: 842 Oak St → 2476 Victoria Ave E
Distance: 3.1 km · Duration: ~9 min
In-trip fare snapshot: $6.82 (55% of trip complete)
Final fare: $12.40 (base $3.50 + dist $3.10 + time $1.08 + fee $1.72 + GST $0.47 + PST $0.56)
```
