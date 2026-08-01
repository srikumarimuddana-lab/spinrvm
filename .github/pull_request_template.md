<!--
Spinr PR template. Required fields are marked [required]. Replace every
<angle-bracketed placeholder> with a real value. CI will fail the PR if any
required field still contains a placeholder.

If this is a `trivial` PR (formatting, typo, comment-only, lockfile-only) you
may delete every section below Tier 1 and mark type=trivial.

Conditional sections (migration, money, UI, auth, background-job, bug-fix,
risk:high) are auto-appended by the pr-checks workflow when the diff warrants
them — you don't need to add them manually.
-->

## Tier 1 — Summary

- **Summary** [required]: <one line — what changed>
- **Type** [required]: `feat` | `fix` | `chore` | `refactor` | `perf` | `security` | `docs` | `trivial`
- **Linked issue** [required]: Fixes #<n>  (use `Refs #<n>` if not a direct fix, or `none` with reason)
- **Risk** [required]: `low` | `medium` | `high` — <one-line justification if medium/high>
- **User-visible change** [required]: `none` | `riders` | `drivers` | `admins` | `corporate` — <one line if not none>
- **Scope contract** [required]: `[ ]` This PR matches its stated type — no unrelated refactors, renames, or cleanups bundled in.

<!-- trivial-stop: if type=trivial you may delete everything below this line -->

## Tier 2 — Impact

- **Surfaces touched** [required]: `[ ]` backend  `[ ]` rider-app  `[ ]` driver-app  `[ ]` admin  `[ ]` shared  `[ ]` migrations  `[ ]` infra  `[ ]` CI  `[ ]` docs
- **Blast radius** [required]: `isolated` | `single-surface` | `multi-surface` | `cross-cutting`
- **Data schema change** [required]: `none` | `additive` | `breaking` | `coordinated-deploy`
- **API contract change** [required]: `none` | `additive` | `breaking` | `versioned`
- **Background job change** [required]: `none` | `new-loop` | `modified` | `deleted`
- **Feature flag** [required]: `none` | `behind-new-flag` | `removes-existing-flag`
- **Config / secret change** [required]: `none` | `new-env-var` | `app_settings-row` | `rotation-required`
- **Rollback plan** [required]: `git-revert-safe` | `revert-plus-data-cleanup` | `coordinated` | `not-revertible` — <one line; include whether old backend talks to new DB if schema changed>
- **Dependencies / coordination** [required]: `none` | <list: PRs that must merge first, mobile release required before backend deploy, secret rotation, etc.>
- **Assumptions** [required]: <one line — what this PR assumes about the rest of the system; write `none` if truly none>
- **Not changing but considered** (optional): <one line — deliberate non-action, if any>

## Tier 3 — Compliance flags

Tick any that apply and fill the elaboration line. At least one reviewer from the flagged area must approve.

- `[ ]` **Money-touching** (fares, wallets, Stripe, corporate billing, payouts, tips, refunds) — <one line>
- `[ ]` **PIPEDA-relevant** (PII fields, logs/analytics payloads, data export/deletion, consent) — <one line>
- `[ ]` **SK Transportation Act** (driver eligibility, trip retention, tax line items, accessibility, surge cap) — <one line>
- `[ ]` **Auth / RLS** (JWT claims, RLS policies, OTP, role checks) — <one line>
- `[ ]` **Safety** (SOS, insurance periods, emergency contacts, night-ride rules) — <one line>
- `[ ]` **Third-party SDK added** — <name + privacy/legal justification>
- `[ ]` **Breaking change to `shared/`** — <one line; list downstream surfaces that need coordinated release>

## Tier 4 — Verification

- **Unit tests** [required]: `added` | `updated` | `not-applicable` — <count or reason>
- **Integration tests** [required]: `added` | `updated` | `not-applicable` — <one line>
- **Metrics / logs introduced** [required]: `none` | <list; confirm naming follows `spinr.<domain>.<metric>.<unit>`>
- **Screenshots / video** [required if UI files touched]: <attach or link>
- **Perf numbers** [required if SLA-critical path touched — dispatch, fare calc, settlement, WS fan-out, driver location, token refresh, Stripe webhook]: <before/after P95>

**Pre-merge checklist** [required]:

- [ ] Ran the changed code against real Supabase dev (not just mocks) — if backend change
- [ ] Tested with rider + driver + admin accounts — if multi-surface
- [ ] Verified the rollback command actually works (not just exists) — if `risk:high`
- [ ] Updated `CLAUDE.md` / `.claude/context/*.md` if a convention changed
- [ ] No PII (raw lat/lng, full phone, email, name, card numbers, gov IDs) added to logs, Sentry payloads, or analytics

<!--
Tiers 5–7 (Conflict & Debug Log, Bug-fix notes, Stop condition, Unmerge
trigger) are appended below by the pr-checks workflow when the diff or branch
history warrants them. Do not fill these until the bot adds the sections —
they are conditional on detected state.
-->
