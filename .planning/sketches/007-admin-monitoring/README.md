---
sketch: "007"
surface: "Admin Dashboard"
name: "admin-monitoring"
question: "Can a dispatcher manage 20 rides without overwhelm?"
winner: "C (Triage Board)"
tags: [admin, monitoring, live, dispatch, kanban]
---

# Admin Monitoring — Sketch 007

## Design Question

Can a single dispatcher maintain situational awareness across 20 simultaneous rides and respond to critical incidents (SOS, dispatch timeout) without information overload?

## Variants

| Variant | Label | Concept |
|---------|-------|---------|
| A | Map-Dominant | Full map + sidebar list; spatial awareness first |
| B | Table-First | Sortable data table + mini-map; operational queue management |
| C | Triage Board ★ | State-column Kanban + live map panel; health visible at a glance |

## Stages Covered

1. **Normal (20 rides)** — healthy fleet, no alerts, searching columns sparse
2. **Alert State** — SOS active (R-1855, Mike D.) + dispatch timeout (R-1852, Tom B., 4m12s)

## Recommendation

**Variant C — Triage Board** wins on the core question. The Kanban model solves the problem the table and map both fail at: **proactive signal without filtering**.

- In normal state, column density teaches system health — a sparse "Searching" column means supply is healthy; a growing one is a leading indicator before any alert fires.
- In alert state, a dedicated "Alerts" column materializes at the far left — spatially impossible to miss without scrolling or filtering.
- State transitions are inherently visible: a ride card moving from "Searching" → "Matched" is more legible than a status badge changing color in a table row.

## Key Design Decisions

- **Alerts column appears dynamically** — only exists when there are incidents; in normal state it's absent, so its presence is itself a signal
- **Card timer on Searching column** — oldest wait times visually pop without sorting
- **Right panel = operational context, not action surface** — mini-map + 4 KPIs (avg wait, match rate, surge, available drivers) inform decisions without competing with the board
- **Alert banners above stats bar** — sticky, with one-tap action buttons; never buried
- **Blinking dot colors** — SOS = red blink, dispatch timeout = gold blink (different urgency levels, different response paths)

## Dispatcher Workflow (Variant C)

**Normal state:** Eyes sweep left-to-right across columns. "Searching" sparse = healthy. No action needed on 18 of 20 rides.

**Alert state:** Red "Alerts" column appears left of "Searching". Dispatcher sees it first (leftmost). Banner at top names both incidents. One-tap "Respond" and "Assign" on the banners handle most cases without drilling into individual cards.

## Realistic Data

```
20 active rides:
- Searching: R-1849 (Emma R. → University, 2:30), R-1852 (Tom B. → Hospital, 1:15)
- Matched: R-1848 (James K., Marcus T., Airport), R-1854, R-1858, ...
- In Trip: R-1847 (Sarah M., Alex J., Midtown), R-1850, R-1853, R-1855, R-1856, ...
- Arrived: R-1851 (Maria G., OTP: 7421), R-1860
- Completing: R-1843

Alert state adds:
- R-1855 SOS: Mike D. + Kim L., in_progress, 1:42 elapsed
- R-1852 timeout: Tom B. → Hospital, 4m12s no driver assigned
```
