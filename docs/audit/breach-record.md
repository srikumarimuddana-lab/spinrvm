# PIPEDA Breach Record Register

**Owner:** Privacy Officer
**Canonical location:** this file (`docs/audit/breach-record.md`) — the file path referenced by `docs/runbooks/data-breach.md`. `docs/dpa-register.md` previously referenced a second, different path (`reports/compliance/breach-register.md`); that reference should be updated to point here so there is exactly one canonical register, not two.
**Retention:** entries are never deleted. Retain indefinitely; 24 months is the PIPEDA-driven floor, not a ceiling — do not purge an entry at 24 months.
**Purpose:** PIPEDA requires organizations to keep a record of every breach of security safeguards involving personal information under their control, whether or not it met the "real risk of significant harm" (RROSH) bar that triggers mandatory notification to the Office of the Privacy Commissioner of Canada (OPC). This register is that record. Populate one row per incident using the template in `docs/runbooks/data-breach.md` (§ "Breach record register").

---

## How to add an entry

1. Follow `docs/runbooks/data-breach.md` end to end for the incident itself (triage, containment, RROSH assessment, notifications).
2. Once the incident is closed, copy the row template below and fill it in completely — including incidents where the RROSH assessment concluded "no" and no external notification was required. This register exists to prove a complete history, not just a list of reportable breaches.
3. Get Privacy Officer sign-off before considering the entry final.
4. Link the post-mortem (`docs/runbooks/data-breach.md`'s 5-business-day SLA) once published.

## Row template

```
### Incident [N] — [YYYY-MM-DD discovery date] — [one-line description]

| Field | Value |
|---|---|
| Discovery timestamp (UTC) | |
| Breach start (estimated, UTC) | |
| Breach end (confirmed, UTC) | |
| Data categories exposed | |
| Estimated number of individuals affected | |
| Real-risk-of-significant-harm (RROSH) determination | yes / no — rationale: |
| OPC notified | yes (date/time) / no (rationale) |
| SGI notified (only if driver licence, insurance, or CRC/VSC data involved) | yes (date/time) / no / not applicable |
| Affected individuals notified | yes (date) / no (rationale) |
| Root cause | |
| Fix deployed | PR # + date |
| Post-mortem link | |
| Incident Commander | |
| Privacy Officer sign-off | name + date |
```

---

## Register

### No entries to date

As of this file's creation, Spinr has not experienced a breach of security safeguards requiring an entry in this register. This is the expected, correct state for a pre-launch/early-launch platform with no incidents — it is not evidence the register is unused or unmaintained. The first real incident (however minor) must be entered using the template above before this line is removed.
