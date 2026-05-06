---
sketch: "001"
surface: "Driver App"
name: "driver-home"
question: "Map-dominant vs panel-first vs status-first architecture"
winner: null
tags: [driver, home, states, map]
---

# Driver Home — Sketch 001

## Design Question

Which screen architecture best serves a professional driver across all 7 operational states?

## Variants

| Variant | Label | Concept |
|---------|-------|---------|
| A | Map-Dominant | Full-screen map with floating glass card. Maximum spatial awareness. |
| B | Split View | Fixed 45/55 split: map above, panel below. Always visible without gesture. |
| C | Status-First ★ | Adaptive layout per state — each state owns its layout entirely. **Recommended.** |

## States Covered

1. **Offline** — App open, driver not working. Single large "Go Online" CTA.
2. **Online / Searching** — Driver available. Map visible, soft earnings row, waiting indicator.
3. **Ride Offered** — 15s countdown urgency moment. Accept/Decline dominant.
4. **Navigating to Pickup** — Turn-by-turn nav mode. Minimal chrome.
5. **Arrived at Pickup** — OTP code display. Driver identity confirmation.
6. **In Trip** — Live fare meter. Route and ETA.
7. **Trip Completed** — Earnings breakdown. 5-star rating prompt.

## Recommendation

**Variant C — Status-First** is the recommended architecture because each driver state has
fundamentally different informational needs. A map is useless during OTP verification; a
large fare meter is useless when waiting for rides. Forcing one layout is always a compromise.
The adaptive approach lets each state be designed optimally without visual conflict.

## Key Decisions

- **Dark-first** — drivers work nights; dark map prevents eye strain
- **Thumb-zone primary actions** — all CTAs at bottom 40% of screen
- **Safety accessible** — SOS in topbar, never buried
- **Earnings gold** (`#D69E2E`) for money to motivate drivers psychologically
- **Online green** (`#38A169`) for status confidence

## Data Used (Realistic)

```
Rider: Sarah M. ★4.9
Pickup: 1720 College Drive, 6 min away
Dropoff: Midtown Plaza
Fare: $16.25 (8.3 km, ~18 min)
Surge: ⚡ 1.5×
Breakdown: base $3.50 + distance $8.30 + time $2.20 + surge $2.75
```
