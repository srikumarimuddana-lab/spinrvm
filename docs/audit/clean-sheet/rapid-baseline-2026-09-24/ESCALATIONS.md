# Escalations — decisions for the founder, legal counsel, and the accountant

Rapid baseline run of 2026-09-24. Every item here is either (a) a tax, legal, or regulatory statement the code relies on that has **no primary source cited anywhere in the repo** (so it is ASSUMED, per master prompt §4), or (b) a product/security decision an agent must not make on its own. Nothing in this file has been changed in code; this is a decision list.

Read the "Decision needed" column first. Each row names who decides and what happens if the assumption is wrong.

## A. Tax and finance — needs an accountant or tax counsel (all ASSUMED, no primary source in repo)

| # | Question | What the code does today (evidence) | Why it matters if wrong | Decision needed | Source |
|---|---|---|---|---|---|
| E1 | **Does Saskatchewan PST apply to ride-share fares?** | All 4 SK service areas run `pst_enabled=false`; receipts show GST 5% only. `backend/features.py:810,838,992`; history in `docs/compliance/2026-08-13-sk-pst-rideshare-determination-needed.md`. The decision has been made three times on verbal signals; every attempt to fetch the SK bulletin was blocked in-session. | If PST applies, every SK ride since the 2026-08-14 revert has under-collected 6% — a live, compounding remittance shortfall. If it doesn't, nothing is owed. | Accountant/SK Ministry of Finance reads the PST bulletin and records the citation (URL + date) in ACTION_ITEMS G9. Set a deadline; this is open since 2026-08-13. | A5 MONEY-003 (ASSUMED, existing item G9) |
| E2 | **Are drivers "taxi businesses" under the Excise Tax Act, so the $30k small-supplier exemption does not apply?** | Payouts are hard-blocked unless a GST/HST business number is on file: `backend/routes/drivers/payouts.py:762-780`. No CRA page cited. | Too strict → drivers who are legally exempt are blocked from being paid. Too loose → drivers under-remit. This gate touches every payout. | Accountant confirms the rule and the citation. | A5 CRA table row 1 (ASSUMED) |
| E3 | **Who is the supplier of record for GST — driver or platform?** | Driver is treated as supplier (independent contractor, `docs/legal/terms-of-service.md:102`); Spinr issues a T4A-style summary, not a GST invoice on the driver's behalf. | Affects receipt content and who owes remittance. | Accountant/legal confirm. | A5 CRA row 2 (ASSUMED) |
| E4 | **Digital-platform operator reporting (Income Tax Act Part XX.1) — does it apply, and what fields/deadlines?** | A SIN-free handoff export exists (`docs/change-log/2026-07-29-t4a-filer-handoff-export.md`), whose author already flagged "not confirmed by a tax advisor". | Missing a filing obligation. | Accountant confirms applicability and deadline. | A5 CRA row 4 (ASSUMED, self-flagged) |
| E5 | **T4A $500 threshold still current?** | `backend/utils/t4a_pdf.py`, `routes/admin/compliance.py` apply $500. No citation in-repo. | Wrong slips issued. | Accountant confirms the figure for the current tax year. | A5 CRA row 5 (ASSUMED) |
| E6 | **Tax treatment of promo credits and driver incentives** | Discount applies to the ride fare only, never fees/taxes (`backend/utils/receipt_pdf.py:163-171`); the tax-on-discounted-fare rule is not traced to a CRA source. | Over- or under-collected GST on discounted rides. | Accountant confirms. | A5 CRA row 8 (ASSUMED) |
| E7 | **Corporate invoices: do they meet input-tax-credit documentation rules?** | Not examined in this run. | Corporate customers can't claim ITCs; churn risk. | Accountant reviews one real corporate invoice PDF. | A5 CRA row 7 (UNKNOWN) |

## B. Regulatory and legal — needs counsel or SGI (ASSUMED)

| # | Question | What the code does today | Decision needed | Source |
|---|---|---|---|---|
| E8 | **Is a driver who declared "online" but became unreachable (app crash, dead phone) still in TNC insurance Period 1?** | `is_online` is pure driver intent; the Period 1 row stays open until the driver taps offline (`backend/utils/presence_sweeper.py:1-31`, deliberate 2026 redesign). This likely gives the driver *more* coverage, not less. | Legal/SGI confirm this interpretation is acceptable. If not, a TTL-based period close-out must be re-introduced carefully (it was retired to fix a worse bug). | A8 SAFETY-003 (design VERIFIED; regulatory reading ASSUMED) |
| E9 | **Audio recording on night rides — one-party consent framing** | Blocked on legal review per `.claude/context/domain-safety.md`; no SK statute cited. | Counsel confirms before any recording feature ships. Nothing to do now. | A8 escalation list (ASSUMED) |
| E10 | **Device fingerprinting at signup vs PIPEDA data-minimization** | No device-id/fingerprint check exists (`backend/routes/auth.py`, zero hits). Same device can create unlimited accounts on different numbers, resisted only by OTP cost + lockout. | Product + privacy decision: accept the promo/referral-farming exposure, or run a privacy impact assessment and add a purpose-limited device signal behind a flag. | A8 SAFETY-004 (VERIFIED gap; remedy is a policy call) |
| E11 | **Certificate pinning — record the decision** | No pinning in either app; the threat model says "OPEN P1" while the standards doc says "deliberate trade-off". Two artefacts disagree. | Founder/security owner picks one and an ADR records it (recommendation from A2: no pinning, rely on TLS + App Check + short tokens, because pinning breaks Cloudflare fail-over). | A2 SEC-A2-004 (VERIFIED contradiction) |

## C. Product and platform decisions — founder

| # | Decision | Why it's yours | Recommendation | Source |
|---|---|---|---|---|
| E12 | **Restore an automated PR review gate.** Claude review is off on cost grounds (C7) and Codex has been silent since 2026-07-30 (C9); ~200 PRs have merged with no automated review, and the required-checks list is stale (C73) so a PR merged 47 seconds after opening (A43). | Cost vs. risk trade-off on a live-tested product. | Turn Claude review back on for PRs that touch money/auth/dispatch/safety paths only (path-filtered, so cost stays bounded), and refresh the required-checks list. | A1 decay signals (VERIFIED) |
| E13 | **Rider notification when a driver is offered the ride (`driver_assigned`).** The doc promises a rider event; the code doesn't send one. | Product choice: show "driver found, confirming…" or deliberately hide it until acceptance. | Decide; then either add the event or fix the doc. Either is small. | A4 DISPATCH-001 (VERIFIED) |
| E14 | **Move JWT signing from one shared secret to a key pair.** Today whoever holds `JWT_SECRET` can mint an admin token. | Security architecture change with a dual-verify window; needs a scheduled cutover. | Approve as a "Next" item behind a flag (`JWT_ASYMMETRIC_MINT`); set `OTP_PEPPER` in production now (zero code). | A2 SEC-A2-001 (VERIFIED) |
| E15 | **Plan the exit from pgsodium** (Supabase says "pending deprecation"; driver SIN and emergency contacts depend on it). | 7-year retention data must never become undecryptable on a vendor's schedule. | Approve an inventory + Vault-only runbook now; envelope encryption with a Canadian KMS as a "Next" assessment. Confirm the deprecation on supabase.com from a machine that can reach it — the lane only saw a search snippet. | A2 SEC-A2-002 (deprecation INFERRED) |
| E16 | **Fill the vendor plan-of-record table.** Supabase compute tier, Redis provider/plan, Sentry quota, Vercel and EAS plans are recorded nowhere in the repo, so no 10× capacity claim can be verified. | Only a human with dashboard access can do this. | 30 minutes in each console; write the numbers into `docs/runbooks/capacity-scaling.md` §5 and `renewal-calendar.md`. | A3-006 (VERIFIED absence) |
| E17 | **Enable the loop process-role split** (`SPINR_PROCESS_ROLE=api` on API machines, one worker). The mechanism is built; the config never set it, so every replica runs all 45 loops. | Production config change on Fly and Railway; env-only, reversible by unsetting. | Approve for staging first, then one production machine at a time. | A3-001 (VERIFIED) |

## D. Things the lanes could not settle and that need a human with system access

- Firebase App Check enforcement state (console-only; ACTION_ITEMS C3) — A2 SEC-A2-005.
- Whether `OTP_PEPPER` is set in the production environment — A2 SEC-A2-001.
- Supabase `list_extensions` on production to confirm the pgsodium version — A2 SEC-A2-002.
- Which Twilio number type (toll-free vs short code) Spinr sends OTPs from — A3 capacity table.
- Whether the mobile apps call the Cloudflare-proxied hostname (affects whether any compression happens at all) — A3-003.
