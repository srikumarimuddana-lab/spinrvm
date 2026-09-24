# A8 — Trust, safety, fraud & privacy

**Lane:** A8 · **Model:** sonnet / spinr-safety-sos-reviewer · **Returned:** 2026-09-24 ~14:25 UTC (partial, 25-minute time box) · **Orchestrator note:** lane output pasted verbatim below; only this header was added. The lane's closing "SAFE TO MERGE" verdict is the agent's habitual PR-review phrasing — there is no PR under review; read it as "no blockers found in the paths examined". PIPEDA retention/deletion numbers (7-year ride / 3-year GPS / 30-day scrub) were NOT reached in the time box — see DEFERRED.md.

---

SPINR TRUST/SAFETY/FRAUD/PRIVACY AUDIT — Lane A8 (partial, time-boxed 25 min)
Repo state as of 2026-09-24. All findings de-duped against ACTION_ITEMS.md B15/B16/B18/B23/B34 where noted.

## (a) SOS hop table

| Hop | path:line | Failure mode | Handled? |
|---|---|---|---|
| Client hold gesture | `shared/components/SOSButton.tsx:~100` (1.2s hold, `SOS_HOLD_MS`) | Accidental trigger | Yes — 1.2s hold gate |
| Client POST + retry | `shared/components/SOSButton.tsx` (3 attempts, 1s/2s backoff, single idempotency_key per press) | Network down on client | Retries 3x, then shows persistent "FAILED — call 911 directly" state (`sos.failure_msg`), never silently drops |
| Backend auth | `backend/dependencies/__init__.py:743` `get_current_user_allow_expired` | Expired JWT mid-trip | **Handled correctly** — signature-valid but expired mobile token (aud=mobile only) accepted; forged/revoked/admin tokens still rejected |
| Idempotency check | `backend/routes/rides/safety.py:138-168` | Malformed key | Logs warning, drops key, still sends alert (fail-open by design) |
| `safety_incidents` insert | `backend/routes/rides/safety.py:203-268` | DB down | try/except → clean 503 ("try again or call 911"), never silently swallowed; **no non-DB fallback exists** — a sustained outage across all 3 client retries = zero contact SMS + zero safety-team notify. This is ACTION_ITEMS.md **B15(a)**, explicitly decided 2026-08-01 not to fix (accepted residual risk). De-duped, not new. |
| SOS on completed ride | Not directly re-verified this pass — `ride_action_limit`/ownership checks apply per endpoint; `trigger_emergency_rideless` (`safety.py:591+`) exists as the no-active-ride path so SOS is never hard-blocked by ride state | INFERRED — not executed |
| SOS pressed twice | `sos_idempotency_key` unique constraint + replay branch (`safety.py:170-182`) returns original incident, `duplicate: true`, no re-send | Handled |
| Admin/safety-team notify | `backend/features.py:2138` `notify_safety_team` | Email/WS failure | 3 independent best-effort channels (WS, email, `logger.critical`); each wrapped so one failing doesn't suppress others. **PII-safe**: logs only `id/category/role/ride_id`, explicitly never the raw description |
| On-call paging | `backend/utils/safety_paging.py::page_on_call` | Webhook down / unconfigured | Best-effort, never raises; **dark by default** (`sos_paging_webhook_url` unset in prod) — known, documented (domain-safety.md), not a live gap today |
| Emergency-contact SMS | `backend/routes/rides/safety.py:352-443` | Twilio down / no contacts | `asyncio.gather(..., return_exceptions=True)`; per-contact failures logged at `logger.error` (not swallowed to warning) with **no raw exception text** (PIPEDA — logs `type(result).__name__` or a status code, never `str(exception)`); per-contact delivery status returned to client, which branches its copy correctly (reached/none reached/no contacts/unknown) — matches domain-safety.md's 2026-08-16 fix |
| 911 offer | `shared/components/SOSButton.tsx:218-356`, `shared/components/SafetyOverlay.tsx:112-230` | — | **Never auto-dials** — `Linking.openURL('tel:911')` only fires on explicit user tap; copy is "Do you want to call 911?" / "you decide" |

## (b) Fraud pattern table

| Pattern | PREVENT / DETECT / NEITHER | Evidence |
|---|---|---|
| Referral self-referral | **PREVENT** | `backend/routes/drivers/referrals.py:254-257`, `backend/routes/users.py:1132` — explicit 400 block, both surfaces |
| Referral velocity | **PREVENT** (contradicts `spinr-fraud-auditor.md`'s stated baseline — code has moved on) | `backend/utils/referral_payout.py:118,156-226` — Redis-backed per-referrer velocity cap (`_DEFAULT_VELOCITY_CAP_PER_REFERRER=5`, admin-tunable via `app_settings.referral_payout_velocity_cap_per_day`), fails closed to allowing payout on Redis error (rate-limit knob, not the payout gate itself — logged at error) |
| Promo stacking | **INFERRED PREVENT, not re-verified this pass** | `backend/routes/promotions.py:452` `compute_promo_discount` exists as single-promo compute path per `spinr-fraud-auditor.md`'s documented pattern; did not re-trace every booking entry point in the time box — treat as INFERRED, not VERIFIED |
| Device + phone reuse at signup | **NEITHER** (confirmed standing gap) | `grep device_id/device_fingerprint backend/routes/auth.py` → no hits. Signup abuse resistance rests entirely on OTP SMS cost + 5-fail/hr lockout. Matches `spinr-fraud-auditor.md`'s documented gap — not new, not worsened this pass |
| GPS spoofing/teleport between pings | **PREVENT** (for settlement/distance), **NEITHER** (for single static fake point in surge/service-area check) | `backend/utils/location_integrity.py:41-175` — `MAX_SPEED_KMH=300`, `TELEPORT_THRESHOLD_KM=10`, `TELEPORT_MIN_SECONDS=10`, unchanged from documented values (no unexplained tuning). Static single-point surge-zone spoofing residual gap is pre-existing/documented, not newly found |
| Driver-rider collusion on fake rides | **NOT VERIFIED this pass** — out of time budget; would need `services/dispatch_service.py`/fare settlement trace | UNKNOWN |
| Cancellation-fee farming | **NOT VERIFIED this pass** | UNKNOWN |
| SMS pumping on OTP | **PREVENT** | `backend/routes/auth.py:413-414` `send_otp` rate-limited `6/minute`; separate resend endpoint `3/minute` (line 767-770); OTP verify attempts separately lockout-gated (`_check_otp_lockout`, line 247, fails **closed** — 503 on Redis error, correct direction for a brute-force gate) |
| Chargeback abuse | **DETECT only (ledger accounting), not fraud-pattern detection** | `backend/services/payment_service.py:529` `record_dispute_close_events` — correctly books Stripe's dispute debit + fee verbatim; no velocity/repeat-disputer flag found in the time box — UNKNOWN whether one exists elsewhere |
| Account takeover via OTP | **NOT VERIFIED this pass** — token_version force-logout-all exists per `dependencies/__init__.py:754` comment, not independently traced | INFERRED |

## (c) Finding cards

### SAFETY-001 — No non-DB fallback for SOS on sustained DB outage
- Hierarchy: L2 Safety › L3 SOS › L4 trigger_emergency › L5 DB outage during SOS
- Severity: MEDIUM (accepted risk, not new)   Priority score: S×B×L = low-medium (rare, but life-safety domain)
- Status: VERIFIED   Existing item: ACTION_ITEMS.md B15(a) — already decided 2026-08-01, not being rebuilt
- Adversary: flaky network / malicious insider (DB outage during active incident)
- Evidence: `backend/routes/rides/safety.py:203-268`
- What happens: rider's SOS press during a sustained DB outage across all 3 client retries produces zero emergency-contact SMS and zero safety-team notify; client shows persistent amber "Not Sent — Call 911 directly."
- Root cause: no Twilio-direct bypass path independent of the `safety_incidents` DB write.
- Recommendation: no action — product decision already made and documented; re-confirming, not re-opening.
- Blast radius: n/a (informational re-confirmation)
- Rollout: n/a
- Verification to close: none — already closed per B15(a); listing here only to satisfy de-dupe requirement, not as a new open item.

### SAFETY-002 — Emergency-contact `relationship` field and OTP-at-add-time still not built
- Severity: LOW   Priority: low
- Status: VERIFIED   Existing item: domain-safety.md's own "Still not built" list (not ACTION_ITEMS-tracked by ID found in this pass)
- Adversary: plaintiff's lawyer (dispatcher lacks relationship context in a real incident)
- Evidence: `.claude/context/domain-safety.md` lines 90-93; not independently re-verified in code this pass beyond confirming `emergency_contacts` schema fields via `routes/rides/safety.py` decrypt helper (name/phone only)
- What happens: a real SOS SMS/dispatch context doesn't tell the safety team who the contact is to the rider.
- Root cause: field stored but never threaded into payload.
- Recommendation: thread `relationship` into `notify_safety_team`/dispatcher view. Alternative: leave as-is (low severity, no reported incident driving it) — reasonable given time/priority tradeoff.
- Blast radius: `backend/routes/rides/safety.py` SMS body construction, `notify_safety_team`.
- Rollout: additive field, no flag needed.
- Verification to close: manual check that dispatcher UI shows relationship.

### SAFETY-003 — `is_online` "pure intent" model means Period 1 row can stay open indefinitely on app crash/WS loss without explicit go_offline
- Severity: LOW-MEDIUM (steelman: likely correct/conservative for coverage, not a gap)   Priority: low
- Status: VERIFIED (design), INFERRED (regulatory correctness)
- Adversary: regulator (SGI) — "was this driver covered when they weren't reachable?"
- Evidence: `backend/utils/presence_sweeper.py:1-31` (docstring explicitly documents the 2026 redesign — sweeper retired, `is_online` is now pure driver-declared intent, reachability lives only in Redis presence and is composed at read time by dispatch, never persisted back onto the period model)
- What happens: a driver whose app crashes or loses connectivity without tapping "Go Offline" keeps an open Period 1 (`driver_insurance_periods`) row and keeps `is_online=true` — dispatch correctly excludes them (via presence AND intent), but the insurance-period audit trail shows continuous TNC contingent coverage for a driver who may be physically unreachable/off-app.
- Root cause: deliberate architectural choice (documented, dated, reasoned — avoids the prior mass-flip bug where Redis-partition false negatives incorrectly zeroed out real online drivers). Trade-off is: correctness of `is_online` for dispatch (fixed) vs. a period row that stays open for longer than the driver was actually reachable.
- Recommendation: confirm with legal/SGI whether "app declared online, unreachable" is intended to retain contingent coverage (this actually likely favors the driver — broader coverage, not a gap that reduces coverage) — flag as a question, not a defect, given the favorable direction.
- Blast radius: none — no code change proposed, this is a confirm-intent flag.
- Rollout: n/a.
- Verification to close: confirm with SGI/legal that extended-but-declared-online coverage during unreachability is acceptable; if not, a TTL-based period closeout would need reintroducing (re-opens the exact bug presence_sweeper.py was retired to fix, so any fix here needs care).

### SAFETY-004 — No device-id/fingerprint check at signup (fraud surface, standing gap)
- Severity: MEDIUM   Priority: medium (money-adjacent — referral/promo farming)
- Status: VERIFIED   Existing item: documented baseline in `.claude/agents/spinr-fraud-auditor.md` §3 — confirmed still true, not a new finding, not worsened
- Adversary: fraudster (SIM-cycling / VOIP numbers to farm first-ride promos and referral rewards)
- Evidence: `grep -n "device_id\|device_fingerprint" backend/routes/auth.py` → zero hits
- What happens: same physical device can create unlimited accounts on different phone numbers, each eligible for first-ride promos and referee-side referral rewards, resisted only by OTP SMS cost + 5-fail/hr lockout.
- Root cause: never built; PIPEDA data-minimization carve-out means this needs a purpose/consent/retention review before adding new device-level collection, not a one-line fix.
- Recommendation: escalate to product for an abuse-vs-friction/PIPEDA-review tradeoff call — not this lane's call to prescribe a specific new PII collection.
- Blast radius: `backend/routes/auth.py` signup path only if a fix is built.
- Rollout: would need a flag + PIA if built.
- Verification to close: product/legal decision, not a code fix.

### SAFETY-005 — Chargeback-abuse pattern detection not confirmed
- Severity: LOW (not verified — could be MEDIUM if genuinely absent)   Priority: low, pending verification
- Status: UNKNOWN
- Adversary: fraudster (serial chargeback filer)
- Evidence: `backend/services/payment_service.py:529-566` handles ledger correctness for a Stripe dispute event but no repeat-disputer velocity/flag logic was found in the time available.
- What happens: unverified — may exist in `services/fraud`-adjacent code not reached this pass, or may be a real gap.
- Root cause: n/a — time-boxed, not traced further.
- Recommendation: a follow-up pass (money/fraud lane) should specifically grep for chargeback-count-per-user logic before this is filed as a confirmed gap.
- Blast radius: n/a.
- Rollout: n/a.
- Verification to close: grep `backend/` for dispute-count/chargeback-rate logic; check admin fraud dashboard if one exists.

## (d) Steelman bullets
- Idempotency-key-per-press design (migration 315) correctly fixed a real double-alert bug (3×3=9 POST problem) without adding retry logic in two places — single point of truth in `SOSButton`.
- `get_current_user_allow_expired` is a narrowly-scoped exception (mobile aud only, forged/revoked tokens still rejected) — exactly matches the CLAUDE.md/domain-safety.md rule without over-broadening auth trust.
- `safety_checkin_loop.py` and `route_deviation_alerter.py` both use the same two-key SET-NX send/escalate idiom, fail at `logger.error`, and re-arm the escalation claim on failure rather than leak state — this is the correct replay-safe pattern CLAUDE.md asks for, applied consistently across two independent loops.
- `notify_safety_team` deliberately omits the raw incident description from its log line specifically because it may contain PII — a proactive, not reactive, PIPEDA safeguard.
- `presence_sweeper.py`'s retirement is a well-reasoned, documented fix to a worse prior bug (mass false-offline flips on Redis partition) — the current "pure intent" model is more correct for dispatch even if it has the Period-1-linger side effect noted in SAFETY-003.
- Referral velocity cap (`referral_payout.py`) has since been built — the codebase has visibly closed a documented gap from the fraud-auditor's own baseline description.

## (e) ESCALATIONS (regulatory/legal claims lacking a primary source)
- SAFETY-003's implicit claim that "declared-online-but-unreachable = still TNC contingent liability coverage" is an SGI/insurer interpretation question — no SGI/Government of Saskatchewan primary source was consulted this pass. **ASSUMED**, needs legal/SGI confirmation.
- The Saskatchewan one-party-consent framing for audio recording (domain-safety.md "Night ride protections" section, "audio recording... blocked on legal review") is asserted in the doc itself without a cited SK statute/OPC page in this pass's evidence trail — carry forward as ASSUMED per the doc's own framing, not independently re-verified here.
- No new regulatory claims were introduced by this audit pass beyond what CLAUDE.md/domain-safety.md already assert; nothing new to escalate beyond the two items above.

## (f) NOT VERIFIED list (ran out of time box)
- Driver-rider collusion on fake rides — not traced this pass.
- Cancellation-fee farming — not traced this pass.
- Account-takeover-via-OTP session-invalidation path (`token_version` force-logout) — read the comment referencing it, did not independently trace the revocation code.
- Promo-stacking enforcement across every booking entry point (only confirmed the existence of the single-promo compute function, not every call site per `spinr-fraud-auditor.md`'s own instruction to check new entry points).
- Chargeback velocity/pattern detection (SAFETY-005).
- Driver-app UI grep for any residual emergency-contact-phone exposure was done at the backend-route level only (`grep emergency_contact` across `routes/drivers/`, `routes/rides/` returned no hits outside `safety.py`) — did not walk driver-app frontend component tree for a UI-level leak (e.g. a debug screen rendering a raw API response).
- `backend/routes/promotions.py` free-ride + wallet double-reward interaction (fraud-auditor §2 "Free-ride promo abuse") not traced.
- SOS-on-completed-ride explicit behavior (whether `/rides/{ride_id}/emergency` 403/409s or still accepts) not read end-to-end — inferred from `trigger_emergency_rideless` existing as the no-active-ride path, not confirmed by reading the ownership/state guard in `trigger_emergency` itself.
- **(Orchestrator addition)** PIPEDA retention/deletion windows (`purge_pii_retention()`, migration 296, 7y/3y/30d numbers), data-export endpoint, consent-version-on-signup, and the insurance-period call-site sweep (Period 2 on `driver_assigned`, append-only check on `driver_insurance_periods`) were in the lane's scope but not reached — see DEFERRED.md.

VERDICT (lane): No BLOCKERS found in the code paths examined (no auto-dial 911 claim, no "we call 911 for you" copy, no emergency-contact-phone driver-facing leak found, no incident-creation swallow, both background loops replay-safe and fail loud). Two WARNINGS carried forward as pre-existing/documented (B15(a) DB-outage fallback, signup device-check gap) and one new confirm-intent question (SAFETY-003) for legal/SGI. Several surfaces (collusion, cancellation farming, chargeback detection, promo-stacking entry points) are UNKNOWN due to the 25-minute time box and should be picked up by a follow-up pass rather than assumed clean.
