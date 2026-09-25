# Spinr clean-sheet audit — executive summary

*25 September 2026. For the founder. Plain language; the evidence is in the linked files.*

## What this is

Twenty specialist reviews looked at all of Spinr — the rider and driver apps, the admin dashboard, the backend, money and tax, safety, compliance, and how the team works. They asked one question: *if Uber or Lyft rebuilt this for Saskatchewan from scratch, what would they keep, and what would they change?* Nothing was changed in code, settings or data.

**The short answer: do not rebuild.** The core is sound. Rides move through their states safely, fares are calculated correctly, the surge cap holds everywhere, and SOS never auto-dials 911. The problems sit at the edges: who gets alerted, what leaves the system, what is switched on, and who owns each piece. Most fixes are settings, decisions, or small code changes.

## How much to trust it

Every claim is labelled: *verified* (someone read the code), *inferred*, or *assumed* (needs a person or an official source). Other reviewers re-checked samples. **Four claims turned out to be wrong, and all four made the same mistake: they said something was missing because a search did not find it.** A trip safety code, a test, a navigation feature and a money safeguard all turned out to exist or to work differently. Those have been withdrawn or corrected. Treat any remaining "X does not exist" claim as likely but not certain.

**Every tax, legal and regulatory statement is unconfirmed.** No government website could be reached during the audit, so each one is a question for an accountant, a lawyer or SGI, not a conclusion.

## The ten decisions only you can make

1. **Rotate the database master key today.** A key that gives full access to every table sat in the public code history for about three and a half months. The repo's own documents disagree on whether it was ever changed. Rotating takes about 15 minutes and costs nothing if it turns out to be the second time.
2. **Decide who gets woken up when someone presses SOS.** Today no automatic page reaches anyone, and the on-call guide describes a system that does not exist. A named phone this week, a proper paging service within a month.
3. **Stop two undisclosed data flows.** Every paid ride sends its value and a hashed identity to Meta's ad system, although Spinr promises "no ad SDKs". A session-recording tool (LogRocket) records iOS screens, including phone numbers and addresses, and sends them to a US vendor. Both can be paused today without an app release. Then decide whether to keep either, and disclose what you keep.
4. **Set pricing and the real revenue model.** With no booking fee, each consumer ride costs Spinr about a dollar (an estimate). There is also no minimum fare and no per-minute rate, so a 1 km ride pays a driver $4. Your legal documents describe revenue lines the product does not have. Change the "100% goes to your driver" wording first, so a booking fee is labelled honestly, then set the fee.
5. **Send one tax question pack to an accountant, with a one-week deadline.** Does Saskatchewan PST apply to fares? It has been open since August; if the answer is yes, the shortfall grows with every ride. Also: whose GST number belongs on receipts; the federal platform-reporting rules; the T4A slip, which currently fills two boxes with the same amount; and the rule that blocks every driver's first payout.
6. **Send one regulatory question pack to SGI, the cities, a lawyer and your broker.** Is Class 5 or Class 4 the correct licence? The code assumes Class 5, and one official snippet says Class 4. Also: municipal ride-share licences and Saskatoon's per-trip fee; SGI's monthly kilometre report; and whether company insurance (general liability, errors and omissions, cyber) is actually in place.
7. **Name an owner for each area.** Today one GitHub account approves almost everything. No one owns the database changes, the alerts, the 44 background jobs, or the SOS response. Pick people, even if one person wears several hats.
8. **Have your GitHub admin check what actually blocks a merge.** Code has merged before its checks finished. One such change lost 53 of 55 payment-failure notices over two months. Everything else in the plan depends on this gate working.
9. **Approve the infrastructure basics.** Stop every web server from also running all 44 background jobs; this is a settings change. Confirm whether a test (staging) environment exists. Schedule one drill that proves failover to the backup host and a database restore — neither has ever been tried. Pick a tool so the published speed targets are actually measured and alerted on.
10. **Approve the rider and driver fairness pack, in a safe order.** Riders get a "dispute this ride" button. Suspended drivers get told why. Drivers learn that declining offers lowers their priority — disclose it, don't remove it. Drivers also get an instant cash-out screen. **The money controls come first:** a daily limit on admin refunds and a speed limit on instant cash-outs. Otherwise a fraudster gets the new doors before the camera is installed.

The full list, with options and who can answer each, is in [`05-escalations.md`](05-escalations.md). The 2026-09-12 driver-data incident is recorded there as **managed by you and closed on your statement of 24 September, not independently checked**. This audit does not re-open it. The separate July key exposure (decision 1) is still open.

## What the plan does, in order

Full detail is in [`ROADMAP.md`](ROADMAP.md). Each item has an owner role, an on/off switch where users could notice, and a way back.

- **This week (mostly settings and decisions):** decisions 1–3 above; add a timeout so a slow SMS provider cannot stall every login; strip precise locations from driver push messages; fix the rounding errors in fares and tips; make the corporate account wind-down safe to retry; send the tax and regulatory packs; correct the documents that are wrong today.
- **Next 2–8 weeks:** turn on the Saskatchewan driver-eligibility checks after a one-week dry run; ship the rider and driver packs behind switches; money correctness; tax fixes once the accountant answers; a second approver for large admin refunds; tighter sign-in security; database-change discipline; fraud reports for staff to review, with no automatic penalties; a first failover drill.
- **Later:** larger clean-ups that only pay off once the basics hold.

**How the rebuild ideas were ranked.** A separate reviewer attacked the architecture plan from eight angles: competitor, driver, rider, fraudster, regulator, operations, finance and staffing. The order that survived:

1. The money guard, rebuilt to cover the writers that actually matter.
2. One way to apply database changes, and one job per server.
3. Small shared building blocks, each backed by an automatic test.
4. Fraud and money controls before new ways to move money.
5. One shared copy of each app screen piece instead of drifting duplicates.
6. A ride history log, added only where code is already being changed.

No new services, vendors or rewrites.

## How to stop finding the same bugs

The same kinds of bug keep coming back: rounding errors in money, errors swallowed silently, fixes applied to one copy of duplicated code but not the other, and documents that go out of date. Seventeen such patterns were found. They recur because many separate AI-assisted sessions each fix the instance in front of them, and the rules exist only as prose. The pattern that has worked here is **an automatic test that fails when the old bug comes back.** The plan builds one per pattern. It also asks you to change one working rule: *fix both copies of duplicated code, or add a test that keeps them in step, before a second fix lands.* See [`06-operating-model.md`](06-operating-model.md).

## Keep

- Driver keeps 100% of the fare.
- Surge is capped at 2.5× and shown before booking.
- Insurance periods are recorded append-only.
- Fare maths uses exact decimals.
- Bookings cannot be double-charged.
- The written reasons behind unusual choices.
- An unusually honest change log.

These are real advantages. They depend on the controls above actually running.

## Not checked

- No live production data, flag settings, secrets or vendor dashboards were read.
- No load test was run.
- The rider and driver apps have no screenshot testing, so every app claim is reasoned from code.
- Every tax, legal and regulatory point awaits a qualified answer.

Every finding, scored and de-duplicated, is in [`matrices/risk-register.md`](matrices/risk-register.md). Who owns what, and where no one does, is in [`matrices/ownership.md`](matrices/ownership.md).
