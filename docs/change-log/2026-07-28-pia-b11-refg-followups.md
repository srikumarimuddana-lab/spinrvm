# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-28 |
| Author | Claude (session task, B11 follow-up) |
| Surface(s) | backend (docs only — no runtime code changed) |
| Domain (Sentry tag) | admin / safety (PII breach-response documentation for the Data Transfer export module) |
| PR / commit link | (filled in on PR) |
| Related issue or gap ID | ACTION_ITEMS.md B11, `docs/privacy/2026-07-28-pia-data-transfer-export.md` recommendations R-E, R-F, R-G |

## 1. Issue / gap identified

Three of the seven PIA recommendations for the Data Transfer admin export module
were still open: R-E (name the module in the breach runbook), R-F (confirm
`notification_preferences` belongs in the export bundle), R-G (legal review of
implied-consent basis).

## 2. Root cause

- R-E: `docs/runbooks/data-breach.md` did not exist at PIA-authoring time, so the
  module couldn't be named in it. It has since been created (independently of
  this task) but was never updated to mention this module, and CLAUDE.md's
  Compliance section still described it as "to be created" — stale.
- R-F: `notification_preferences` was included in the export bundle by original
  implementation default, without an explicit "is this field needed" check.
- R-G: requires a human legal/privacy sign-off Claude cannot perform.

## 3. Fix / remediation

- R-E: added a dedicated §1a-i section to `docs/runbooks/data-breach.md` naming
  the Data Transfer export module's specific data flow (full unredacted PII, up
  to 100 entities, GPS precision, government ID numbers) as a designated
  high-sensitivity flow, with concrete containment commands (revoke
  `data_transfer_export_jobs.expires_at`, disable the route, scope-query by
  `created_at` window). Corrected CLAUDE.md's stale "(to be created)" note.
  Updated the PIA doc's §7 and R-E entry to record resolution.
- R-F: reviewed `notification_preferences`' actual contents (boolean
  opt-in/opt-out toggles only, no PII) and the module's stated purpose
  (reconstructing a working account in a target environment). Determined the
  field should **stay** in the bundle — dropping it would silently revert a
  migrated user's notification settings to defaults, a real fidelity
  regression for the module's actual use case, with no meaningful risk
  reduction (the module's real risk drivers are GPS + government ID + document
  bytes, not this field). **No code change** — documented reasoning in the PIA
  doc §8 and marked R-F resolved-as-is.
- R-G: left open in ACTION_ITEMS.md, reworded to explicitly state it requires
  human legal/privacy counsel and is not resolvable by an engineering task.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated to documentation.** No application code, migrations,
  routes, or tests were touched. `notification_preferences` continues to be
  queried/exported exactly as before (`entity_export_service.py:65,113`,
  `bundle_zip_builder.py:92`) — R-F's outcome is "no change," so nothing to
  regress there.
- Grepped for other consumers of `notification_preferences` in the export path
  to confirm no other caller depends on it being removed: `bundle_zip_builder.py`
  (writes it into the ZIP), `test_data_transfer_export_route.py`,
  `test_data_transfer_zip_builder.py`, `test_entity_export_service.py` (all
  reference it as an expected present field — consistent with "keep").
  `test_dsar_export.py`/`test_dsar_export_gather.py` reference a *separate*
  self-export (DSAR) flow, not this admin module — unaffected either way.
- `docs/runbooks/data-breach.md` changes are additive (new subsection); no
  existing section renumbered or removed, so cross-references from
  `docs/runbooks/security-incident.md` and elsewhere are unaffected.

## 5. User-experience effect

None. This is internal documentation for admins/incident-responders and a PIA
record; no rider/driver/corporate-admin-facing behavior changed, and nothing is
visible mid-session to any user of the app.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `docs/runbooks/data-breach.md` | Added §1a-i naming the Data Transfer export module as a designated high-sensitivity breach flow, with containment commands | R-E |
| `docs/privacy/2026-07-28-pia-data-transfer-export.md` | Updated §7 and R-E/R-F entries to record resolution and reasoning | R-E, R-F |
| `CLAUDE.md` | Removed stale "(to be created)" note now that the runbook exists | R-E |
| `ACTION_ITEMS.md` | Updated B11 section: R-E and R-F marked done with summary, R-G reworded to explicitly require human legal review | R-E, R-F, R-G |

## 7. Before / after

Documentation-only additive change (new subsection, corrected stale parenthetical); no behavior-changing diff to code.

```
# Before (CLAUDE.md)
- See `docs/runbooks/data-breach.md` (to be created) for the full procedure
```

```
# After (CLAUDE.md)
- See `docs/runbooks/data-breach.md` for the full procedure
```

## 8. Rollback plan

Pure documentation change — `git revert` of this commit is sufficient and
complete (no live data, no migration, no deployed behavior changed).

## 9. Verification performed

- [x] Blast-radius grep performed: searched for `notification_preferences` across
  `backend/services/data_transfer/`, `backend/routes/admin/data_transfer*`, and
  `backend/tests/` to confirm all consumers before making the keep/remove
  determination for R-F (listed in §4 above).
- [x] Reviewed against relevant CLAUDE.md conventions: PIPEDA data-minimization
  principle (for R-F's determination) and the breach-protocol section (for R-E).
- [ ] No automated tests run — no code changed, nothing to test.
- [ ] Not verified in staging — documentation-only, no deployable artifact.
- [ ] Not feature-flagged — not applicable, no user-visible behavior.

## 10. Sign-off

- [x] Rollback plan is concrete and testable (plain `git revert`)
- [x] Blast radius is stated, not assumed (isolated to docs; R-F consumers enumerated)
- [x] No silent behavior change — no behavior changed at all (docs-only); R-G explicitly left open pending human legal review, not silently marked done
