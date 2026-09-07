# Memory — Cross-Session Decisions & Resolved Ambiguities

This file is the project's memory log: durable, cross-session context that isn't ADR-worthy
(no significant *technical* choice with consequences) but shouldn't be re-litigated or
re-discovered from scratch every time it comes up. It's distinct from the `domain-*.md` files
in this directory, which are reference material for a subsystem, not a decision history.

**When to add an entry here:**
- An ambiguous request got resolved a specific way and the reasoning would otherwise be lost
  (e.g. "does X mean A or B" — asked, answered, here's the answer so it isn't re-asked).
- A recurring question got a standing answer (e.g. "why doesn't Y do Z").
- A past assumption turned out wrong and got corrected — record the correction, not just the fix,
  so the same wrong assumption doesn't get made again elsewhere.

**When NOT to add an entry here:**
- A significant, reversible-with-cost technical decision with real consequences → that's an ADR
  (`docs/adr/`), not this file.
- Anything that already has a durable home — regulatory rules (`regulatory-sk.md`), domain
  mechanics (`domain-*.md`), brand assets (`brand-spinr.md`), or a Change Impact Log entry
  (`docs/change-log/`) — link to it instead of duplicating it here.
- Sprint/task status — that belongs in `ACTION_ITEMS.md`, not here.

**Format:** one entry per resolved item, newest first. Keep each entry to a few lines — this is
a pointer/rationale log, not a narrative.

---

## Entries

_(none yet — this file was created 2026-09-07 as part of a documentation-completeness pass;
add entries here going forward instead of letting resolved ambiguities evaporate at the end of
a session)_
