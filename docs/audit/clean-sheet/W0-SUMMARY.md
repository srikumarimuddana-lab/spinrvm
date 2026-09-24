# W0 summary — input for every W1/W2/W3 lane (≤ 30 lines)

Sources: `00-history.md` (Historian, full GitHub history PR #1 → #5756, 1,470 change-logs scanned, 52 workflows read) and `01-inventory/epics.md` + `traceability.csv` (Cartographer, 1,788 rows). Written 2026-09-24. Labels: V = VERIFIED, I = INFERRED.

**How Spinr got here (00-history.md)**
1. The merge gate is advisory in practice (V): `main`'s required-checks list was never audited (C21 open since 08-12); PR #5048 merged in 47 s with a red check and shipped B42 (53 of 55 `payment_failed` webhook events lost since 07-15). Every other gate inherits this.
2. Duplication-by-default with warn-only parity (V): CarMarker ×5 plus a third unregistered copy (admin track page, fixed 09-22), three FCM exclusion sets, two notification inboxes, two auth files, two insurance-period implementations, three OTP systems. CLAUDE.md's "surgical change" rule is producing this.
3. "Never swallow errors" is prose (V): fixed 08-01, 08-11, 08-12, 09-11 and open again in C135 (7 of 8 sites). The static-test mechanism that closed the loguru class (C60/C65/C69) has not been pointed at this class.
4. Docs are snapshots (V): loop count is 44 today vs CLAUDE.md 42 (drifted five times); "admin JWT fully trusted" is false in 3 files; capacity runbook describes the pre-09-16 fleet; `domain-dispatch.md:40` promises a rider event no code emits. **Erratum to the rapid baseline: Codex reviews resumed 2026-09-16 (#5480 → #5748); CLAUDE.md and C9 still say silent.**
5. Production schema is applied by hand (V): 116 migration files untracked (G2), 8 pending today (C125) including the RLS enable for `settings` (holds Stripe/Twilio/Maps keys; C43, deferred since 08-25); apply needs a `DATABASE_URL` no session has.
6. 17 recurrence families total, incl. 13 "found while fixing the previous" float-on-NUMERIC chains, 68 duplicate migration prefixes, 9 duplicate `###` ids in ACTION_ITEMS (A34, A40, C100, C111, C112, C118, C129, C13, G2), 7 non-atomic CAS items, DST ×5, 3,206 zero-duration Maestro runs.
7. Hotspots (commits): `admin-dashboard/src/lib/api.ts` 234, `routes/admin/drivers.py` 143, `admin/rides.py` 131, `routes/auth.py` 128, `ci.yml` 122, `webhooks.py` 109, `rideStore.ts` 102; most change-log-cited: `core/lifespan.py` 108.
8. 38 decay signals; Playwright e2e is `continue-on-error` on every PR (new); 21 live `continue-on-error` lines; watchdog alerts no-op unless `ALERT_WEBHOOK_URL` is set (UNKNOWN).

**What exists (01-inventory)**
9. 18 L2 epics (rapid baseline's 14 + AI Assistant, Engineering Gates & CI/CD, Shared Frontend Foundation, Platform Foundation & Schema); ~90 scaffold stories `S-<epic>-<nn>` because `docs/PRD.md` has no story IDs; 693 HTTP endpoints, 45 loop-registry entries, 553 migrations, ~90 mobile screens, 43 admin pages, 52 workflows.
10. Row status: 996 mapped, 769 orphan, 16 gap, 7 sibling-missing; evidence 101 V / 1,671 I / 16 UNKNOWN. Orphans are mostly admin-dashboard (415 units on an 8-bullet PRD footprint) and `shared/` (93 files, 2-sentence PRD footprint, root of 3 live cross-app bugs).
11. **Do not cite the `gap` column at face value**: all 4 spot-checked gaps were keyword-collision misroutes of real code (CARTO-004). Migration applied-status is UNKNOWN for every row (CARTO-005).
12. Unowned surfaces: Maps & Routing (`maps_proxy.py`, `service_areas.py`, `h3_heatmap.py`, venues) has no `spinr-*` agent despite gating WAV dispatch; no agent owns `shared/` logic correctness (CARTO-002/003).

**Instructions for W1–W3 lanes**: start from your epic's rows in `traceability.csv` (filter `epic`), treat `orphan` rows as candidate findings (dead code / undocumented scope / missing requirement), re-check any `gap` row before citing it, cite HIST-nnn / CARTO-nnn ids when a finding re-confirms one, and never trust CLAUDE.md counts (loops, review-gate status) without re-reading the code.

**Human-only questions carried forward** (`00-history.md` §9): branch-protection contents; whether the 2026-07-30 service-role key was rotated (repo docs disagree); GitHub PII-purge ticket status; why Codex stopped/resumed; `ALERT_WEBHOOK_URL` set?; migration owner; actual launch phase and user counts; old-app decommission date; device access for marker fixes; staging existence; Actions budget; appetite to change the "surgical" rule.
