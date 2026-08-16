# Safety Toolkit — Gap Analysis vs. Uber, and What's Feasible for Spinr

**Date:** 2026-08-16
**Status:** Analysis / proposal — no code changed by this document.
**Trigger:** Screenshot of the **Uber Driver** app's "Safety Toolkit" (Regina, SK) showing four
entries: *911 assistance*, *Follow my ride*, *Proof of trip status*, *Safety Hub*. Question asked:
what do we already have, what's feasible, and does a per-service-area "city authority" contact
(shown only when configured) make sense?

> Note on the screenshot: the `GO` button and the `$7.52` earnings pill identify this as the
> **Driver** app, not the rider app. Uber ships a near-identical toolkit on the rider side, so this
> analysis covers both surfaces but flags where our driver/rider parity differs.

---

## 1. Executive summary

| | |
|---|---|
| **Uber's 4 toolkit entries** | We fully have **1** (911), partially have **1** (Follow my ride), don't have **2** (Proof of trip status, Safety Hub) |
| **Where we're ahead of that toolkit** | Discreet silent-SOS shield, emergency-contact SMS fan-out, automated 20-min safety check-in with escalation, admin incident queue, on-call paging hook, SOS that survives an expired JWT |
| **Where we're behind** | No consolidated Safety entry point; no law-enforcement "proof of trip"; share-trip is fragmented and partly broken |
| **Biggest surprise found** | Share links minted through the rider's own "Share my trip" button **never expire** (see F3) |
| **Your "city authority" idea** | Sound instinct, and the per-area conditional-render pattern already exists in this codebase — but it must be **new additive columns**, not a reuse of the existing `regulatory_authority` field, and it belongs in a Safety Hub, **not** next to the 911 button |
| **Calgary changes its priority** | 311 is the City of Calgary's designated rideshare complaint channel under Livery Transport Bylaw 20M2021 — so this field is an **expansion launch gate**, not a nicety (§3.4). Chasing it also surfaced a driver-eligibility conflict that is bigger than this analysis (§3.5) |

---

## 2. What we already have (verified in code)

### 2.1 Mapped against Uber's four entries

| Uber entry | Spinr status | Evidence |
|---|---|---|
| **911 assistance**<br>*"Contact emergency services."* | ✅ **Have, both apps.** Never auto-dials — opens the dialer and the user decides. Matches our "Not a 911 replacement" rule. | `shared/components/SafetyOverlay.tsx:101,182-191` (driver, flag-gated), `shared/components/SOSButton.tsx:116-133` (both apps, offered after a confirmed alert) |
| **Follow my ride**<br>*"Share your location and trip status."* | 🟡 **Partial.** Backend + public tracking page are solid. Rider surface works. Driver surface is **wired but non-functional** (see F1). No auto-share. | `backend/routes/rides/sharing.py` (`GET`/`POST /rides/{id}/share`, public `GET /rides/track/{token}`), `rider-app/app/ride-in-progress.tsx:322-346`, `rider-app/lib/shareTripMessage.ts` |
| **Proof of trip status**<br>*"Show law enforcement your status."* | ❌ **Don't have.** No screen, no endpoint. Building blocks all exist though — see 4.5. | — |
| **Safety Hub**<br>*"View your safety settings and resources."* | ❌ **Not as a hub.** The pieces exist but are scattered across the Account menu with no safety home. | `rider-app/app/(tabs)/account.tsx:290,292` → `emergency-contacts.tsx`, `report-safety.tsx`; driver has the same two screens, also unlinked |

### 2.2 What we have that this toolkit doesn't show

These are real assets. Several are genuinely stronger than the Uber surface in the screenshot:

| Capability | Where | Notes |
|---|---|---|
| **Discreet silent SOS ("Hold Shield")** | `shared/components/SafetyShield.tsx` | Driver-app only. Hold 3 s → silent alert (badge + small toast, never an interruptive `Alert.alert`); short tap → full Safety overlay. Built for the threat model where a passenger must not see the screen change. **Dark-launched** behind `app_settings.driver_discreet_sos_enabled`, default `False` — i.e. **not live today**. |
| **Emergency-contact SMS fan-out on SOS** | `backend/routes/rides/safety.py:175-246` | Up to 5 contacts, sent concurrently via `asyncio.gather`, with a **per-contact `notified` true/false** returned so the UI can show "✓ Notified" per person rather than a bare count. |
| **SOS survives an expired token** | `backend/routes/rides/safety.py:46` | `get_current_user_allow_expired` — SOS is never gated behind an auth refresh. Ride membership is still enforced. |
| **Automated safety check-in** | `backend/utils/safety_checkin_loop.py` | Ride `in_progress` ≥ 20 min → silent push "Are you okay?"; no response in 90 s → auto-escalates into an open safety incident. Replay-safe across replicas via three Redis keys. |
| **Admin safety incident queue** | `backend/routes/admin/safety.py` | List / detail / create / patch / merge. WS broadcast to admins on every SOS. |
| **On-call paging hook** | `backend/utils/safety_paging.py` | PagerDuty-shaped, provider-agnostic webhook. Payload carries IDs + a **geohashed** area only — never raw lat/lng or name (PIPEDA). **Dark** — `sos_paging_webhook_url` unset, no paging account exists yet. |
| **SOS confirmation push to the triggering user** | `backend/routes/rides/safety.py:157-173` | `priority="safety"` guaranteed-delivery tier, so it lands even if the app was backgrounded/killed. |
| **Zoho Desk ticket on safety report** | `backend/routes/safety.py:114` | Fire-and-forget, urgent priority. |

---

## 3. Your "city authority" idea — assessment

**The idea:** alongside 911, offer a local/city authority contact, configured per service area, that
renders only when it's been filled in for that area.

**Verdict: feasible, small, and the conditional-render instinct is correct — with two important
corrections.**

### 3.1 The pattern already exists here

`backend/migrations/223_service_areas_regulatory_authority.sql` already added to `service_areas`:

```sql
regulatory_authority          TEXT   -- 'SGI', 'Calgary Livery Transport Services', 'Toronto PTC'
regulatory_region             TEXT   -- 'SK', 'AB', 'Calgary', 'Toronto'
regulatory_requirements_url   TEXT
regulatory_notes              TEXT
```

Admin already edits these (`admin-dashboard/src/app/dashboard/service-areas/page.tsx:734`), with
province-based defaults. So "admin fills a per-area authority, blank means not applicable" is a
**proven pattern in this codebase**, not a new invention.

### 3.2 Correction 1 — do NOT reuse those columns

Those four fields are **driver licensing/regulator** metadata. Their only consumers today are
`backend/services/driver_import_service.py:136-140,361` (mapping `sgi_approved` → 
`regulatory_authority_approved` on import) and the admin drivers page. They carry **no phone
number**, and their semantic is "who licenses drivers here," not "who does a rider call."

Repurposing them for a rider-facing emergency contact is exactly the anti-pattern CLAUDE.md's
release gates forbid: *"Never repurpose a column's meaning without a migration + dual-read window."*

**Do instead:** new additive columns, e.g.

```sql
ALTER TABLE public.service_areas
  ADD COLUMN IF NOT EXISTS safety_authority_name  TEXT,
  ADD COLUMN IF NOT EXISTS safety_authority_phone TEXT,
  ADD COLUMN IF NOT EXISTS safety_authority_url   TEXT,
  ADD COLUMN IF NOT EXISTS safety_authority_hours TEXT;   -- e.g. 'Mon-Fri 8:00-16:00 CST'
```

### 3.3 Correction 2 — placement matters more than the feature

A non-911 number rendered next to the 911 button in an active-emergency panel is a genuine safety
risk: under stress people tap the first plausible thing, and a municipal licensing line that answers
in three business days costs seconds that matter. This is the same reasoning behind our existing
"never auto-dial 911" and "never claim to replace emergency services" rules.

**Recommendation:**
- **Safety Hub / non-emergency section** → yes, show it. Frame it as *"Report a licensing or conduct
  concern"* or *"Local transport authority"*, with hours.
- **SOS / active-emergency panel** → no. 911 stays the only phone action there.

### 3.4 Per-city reality check — and why Calgary makes this a launch gate

**Saskatchewan (Regina / Saskatoon)** — ride-share is regulated provincially (SGI Auto Fund) plus
municipal business licensing. There is no dedicated municipal ride-share hotline. Honest default
here: name + URL populated (SGI), phone **blank** → renders as an informational link, not a call
button. This is exactly why the conditional render is the right design.

**Calgary — the field is not optional.** The City of Calgary regulates ride-share as a
"Transportation Network Company" under **Livery Transport Bylaw 20M2021** (Council-approved
2021-03-22), administered by Livery Transport Services. **311 is the City's designated complaint
channel for rideshare** — by phone or the 311 mobile app — and the City states it investigates
reported concerns. So for a Calgary service area the row is fully populated:

```
safety_authority_name  = 'City of Calgary 311'
safety_authority_phone = '311'
safety_authority_url   = 'https://www.calgary.ca/taxis-ride-share/tnc.html'
safety_authority_hours = '24/7'
```

That reframes this feature: in SK it's a *nice-to-have informational row*; in Calgary it's part of
operating legitimately in the market. It should be treated as an **expansion launch gate**, not a
Tier-2 nicety — which is why it stays in Sprint 2 rather than sliding.

Note `'311'` is a 3-digit service code, not an E.164 number. Whatever validation goes on
`safety_authority_phone` must accept short codes (311, 211, 811) — a naive phone regex will reject
the single most important value this column will ever hold.

### 3.5 Calgary surfaces a much larger gap than the phone number

Chasing the 311 requirement turned up driver-eligibility rules that our engine cannot currently
express. Calgary requires, for a Transportation Network Driver's Licence (TNDL):

| Calgary requirement | Our state |
|---|---|
| Alberta driver's licence **Class 1, 2 or 4**, ≤ 9 demerits | ❌ **Direct conflict.** CLAUDE.md's driver-eligibility rule hardcodes *"Valid Class 5 driver's license (standard) — Class 1-4 drivers need separate approval."* In Alberta, Class 5 is the ordinary licence and **Class 4 is the commercial class Calgary actually mandates**. Our stated rule would reject exactly the drivers Calgary requires. |
| Police Information Check **with vulnerable sector**, dated within **60 days** | 🟡 We require CRC + Vulnerable Sector Check renewed annually — but not the 60-day-at-application freshness window. |
| Vehicle inspection (AB Motor Vehicle Record of Inspection or Enhanced Livery standard) within **30 days** of application | 🟡 We require annual inspection + vehicle < 10 years; no per-area recency rule. |
| Mandatory **Livery Driver Training Program**, ≥ 80% pass | ❌ No training/certification concept exists in the onboarding model at all. |
| Affiliation with a City-licensed TNC; rides and payment only through a **City-approved app** | ❌ Platform-level licensing obligation, not a code change — flagging it, not scoping it. |

Two concrete code facts behind that table:

- **`service_areas.required_documents` (JSONB) already exists** and is genuinely load-bearing —
  `backend/documents.py:475-494,591,722,928-946`, `backend/onboarding_status.py:144-164`, and
  `backend/services/driver_import_service.py:516` all resolve a driver's required docs from their
  service area. So the *document* half of Calgary's requirements (PIC, inspection, training
  certificate) is expressible today with **no schema change** — it's data entry plus, at most, a
  per-document recency field.
- **`license_class` is stored but never enforced.** It's imported
  (`driver_import_service.py:135,697`), displayed in admin (`drivers/page.tsx:1323,1366`), there's a
  whole backfill screen for it, and it's exported onto SGI forms
  (`services/data_transfer/sgi_field_maps.py:72`) — but grep finds **zero** eligibility checks
  against it. `go_online` gates on document expiry only. So the Class-5 rule in CLAUDE.md is a
  *documented* policy that no code enforces, which is why the Calgary conflict hasn't bitten yet.

**Implication:** the fix for Calgary is not "add a per-area licence-class rule" bolted onto a
hardcoded assumption — it's that eligibility should be **per-service-area data**, the way
`required_documents` already is. That is a materially bigger piece of work than this safety
analysis, and it belongs in its own scoping doc under expansion, not here. Recorded so it isn't
rediscovered later.

### 3.5 Delivery path — already open

`GET /service-areas` (`backend/routes/service_areas.py:59-83`) projects through a whitelist
(`_PUBLIC_FIELDS`, lines 31-42). Adding the new fields there is purely additive — no client breaks,
and clients that don't read them ignore them. The apps already resolve a service area per
driver/ride.

**Effort: ~S.** One migration, four lines in `_PUBLIC_FIELDS`, an admin form section, a conditional
block in the (new) Safety Hub. **Risk: Low** — additive, nothing reads or writes it today.

---

## 4. Findings & recommendations, ranked

### Tier 1 — Fix what's already broken or misleading (do these first)

#### F1. Driver's "Share Live Trip Link" button does nothing the driver can see 🔴

`shared/components/SafetyOverlay.tsx:103-111`:

```ts
const shareTripLink = async () => {
  try {
    await api.get(`/rides/${rideId}/share`);   // response discarded
  } catch { /* best-effort */ }
};
```

The share URL is fetched and thrown away. No share sheet, no clipboard, no display. A driver taps
"Share Live Trip Link" in a safety overlay and **nothing happens** — the worst possible failure mode
for a safety control, because it reads as success.

Rider-side does this correctly (`rider-app/app/ride-in-progress.tsx:322-346` builds a message and
opens the native `Share` sheet). Mitigating factor: this is behind the dark
`driver_discreet_sos_enabled` flag, so no live driver has hit it yet — but it **must not** be flipped
on before this is fixed.

**Effort: XS.** Reuse the rider's `buildShareTripMessage` + `Share.share()`.

#### F2. There is no "Safety" entry point in either app 🟠

Emergency Contacts and Report a Safety Issue sit as two unrelated rows in the rider Account menu
(`rider-app/app/(tabs)/account.tsx:290,292`). A rider who wants to *check* their safety setup has no
place to go. This is the single highest perceived-parity gap vs. the screenshot — Uber's "Safety
Hub" is what makes the other three feel like a system rather than scattered buttons.

**Effort: S.** A new `safety-hub.tsx` per app, linking existing screens + the new authority block
(§3) + share-trip + "how SOS works" copy. No new backend.

#### F3. Share links created via the rider's own share button never expire 🔴 (security / PIPEDA)

Two writers, one of them incomplete:

| Path | Sets `shared_trip_token` | Sets `shared_trip_token_created_at` |
|---|---|---|
| `GET /rides/{id}/share` (`sharing.py:62-65`) | ✅ | ❌ |
| `POST /rides/{id}/share` (`sharing.py:102-114`) | ✅ | ✅ |

And the public tracking endpoint only expires when the timestamp is present
(`sharing.py:189` — `if token_created:`). So a token minted by `GET` is **permanently valid**.

The rider app's "Share my trip" calls **`api.get`** (`ride-in-progress.tsx:337`), and so does the
driver overlay. In practice the primary user-facing share path is the one that produces
non-expiring links. A link forwarded once keeps exposing pickup/dropoff addresses, live driver
coordinates, plate, and photo indefinitely.

Also note the doc drift: `.claude/context/domain-safety.md:85` promises *"Link expires 2 h after
`completed`"*; the code implements 24 h from creation. Neither matches the other.

**Effort: XS** (stamp the timestamp in the `GET` path; decide and align on one expiry rule).
**Risk:** touches a live surface — existing un-stamped tokens would need either a backfill or a
"treat NULL as expired-at-ride-end" rule. Worth a Change Impact Log entry.

#### F4. `domain-safety.md` documents four things that don't exist in code 🟠

Verified absent by grep across the repo:

| Doc claim (`domain-safety.md`) | Reality |
|---|---|
| L50 — emergency contacts *"stored encrypted (`pgcrypto`)"* | Plaintext `TEXT` (`migrations/120`, `08_complete_schema.sql:328-335`). No pgcrypto anywhere near this table. |
| L51 — contact phone *"validated at add-time via OTP ping"* + STOP opt-out | No such flow exists. We SMS these numbers during an SOS with no consent handshake. |
| L50 — *"Max 5 per user"* | Enforced only as `limit=5` on the **read** in `safety.py:180`. No DB constraint, no insert-time check — a user can store more; the extras just silently never get notified. |
| L91-94 — night-ride *"audio recording"* + *"route-deviation alert"* | Neither exists. (`utils/route_validation` computes `deviation_pct` for **GPS-spoofing fraud detection**, not safety alerting.) Forced check-in **does** exist, via `safety_checkin_loop.py`, but at a 20-min threshold, not the documented 15-min mark. |

The consent gap is the sharp one: we send SMS to third parties who never opted in. Under PIPEDA
that's a real exposure, not just a doc bug.

**Effort: XS to correct the doc; M to build the OTP/STOP flow.** Recommend correcting the doc
**this week** and tracking the build separately — a doc that overstates safety coverage is worse
than no doc.

### Tier 2 — Real parity features

#### F5. "Proof of trip status" screen (Uber parity) 🟢

The most self-contained new feature on this list, and the one with the clearest local justification:
a Saskatchewan roadside check where an officer asks a driver to prove they're on a platform trip.

Everything needed already exists — nothing new to compute:

| Element | Source |
|---|---|
| Trip reference `SPR-XXXXXX` | `rides.ride_code` (`utils/ride_code.py`, migration 40) |
| Driver name, plate, vehicle make/model/colour/year | `drivers` (already projected for the public tracking page, `sharing.py:218-230`) |
| Current status | `rides.status` |
| Insurance period 0-3 | derivable from ride state per the CLAUDE.md table; `driver_insurance_periods` has the audit row |
| Company / regulator | `service_areas.regulatory_authority` — **this is what that column is actually for** |

Design constraint: this screen is shown to a **third party standing outside the car**, so it must
show *no rider PII* — no rider name, no phone, no exact addresses. Status + vehicle + trip ref +
insurance period only.

**Effort: S-M.** One read endpoint + one screen per app. **Risk: Low** (read-only). Pairs naturally
with §3's authority field — same regulatory framing, same screen family.

#### F6. Per-service-area safety authority contact — **your idea**, as scoped in §3 🟢

**Effort: S. Risk: Low.** Blank in SK, populated with 311 in Calgary. Phone validation must accept
3-digit service codes (§3.4). Treat as an **expansion launch gate** for any Calgary rollout, not a
discretionary Tier-2 item.

**Related but out of scope here:** §3.5's per-area driver-eligibility gap (Calgary mandates a
commercial licence class our documented policy rejects; no training-certification concept exists;
`license_class` is stored but never enforced). Needs its own scoping doc under expansion — do not
let it ride along on a safety PR.

#### F7. Auto-share / trusted contacts ("Follow my ride" as Uber actually ships it) 🟡

Uber's "Follow my ride" is not just a share button — it can be set to share **every** trip, or every
night trip, with chosen contacts automatically. We have zero auto-share
(`grep auto_share|trusted_contact|always_share` → no hits). Today the rider must remember to share
during the moment they'd least remember to.

Given we already have `emergency_contacts` and a working share-token system, this is mostly
plumbing: a per-contact `auto_share` flag + a hook at `in_progress` that fires the existing
`POST /rides/{id}/share`.

**Effort: M.** **Risk: Medium** — auto-sending a location link is a consent-sensitive act; needs
explicit rider opt-in per contact, and F4's OTP/STOP gap should be closed **first** so the recipient
has a way out.

#### F8. Flip `driver_discreet_sos_enabled` on 🟡

We built the discreet shield, tested it under Jest, and it's been dark since 2026-08-11. It has
**never run on a real device** (per ACTION_ITEMS B16's explicit "NOT verified" block: gesture timing,
toast rendering, and flag-on visuals are not Jest-testable).

**Blocked on:** F1 (the dead share button is inside this flag's surface) + a real-device QA pass.
**Effort: XS to flip, S for the QA pass.**

#### F9. Rider-side discreet overlay parity 🟡

Sketch 010 chose a *different* winning design for the rider (tap → overlay → visible 2 s hold inside
it), on the reasoning that the rider's threat model doesn't demand silence the way the driver's
does. It was never built; rider-app still uses the plain `SOSButton`. Explicitly out of scope of
B16 and untracked anywhere today.

**Effort: M.** Worth doing *after* the Safety Hub (F2), since the hub is the natural container.

### Tier 3 — Bigger, or needs legal sign-off

| # | Feature | Effort | Blocker |
|---|---|---|---|
| F10 | **Audio recording** (documented in `domain-safety.md`, doesn't exist) | L | **Legal first.** Saskatchewan is one-party consent for recording, but a platform recording *both* parties needs explicit ToS consent from each, plus storage/retention/access rules. Do not build before sign-off. |
| F11 | **RideCheck-style anomaly detection** (crash, long unexpected stop, major route deviation) | L | Needs the driver-location stream to feed a detector. We already ingest pings and compute `deviation_pct` for fraud — the signal exists, the safety pipeline doesn't. |
| F12 | **PIN verification at pickup** (rider reads a 4-digit code to the driver) | M | Prevents wrong-car entry, an industry-standard control. `ride_code` exists but is an ops reference, not a pickup challenge. |
| F13 | **Emergency-contact OTP + STOP opt-out** (F4's consent gap, built rather than documented away) | M | PIPEDA-motivated. Prerequisite for F7. |

### Tier 0 — The in-ride SOS button itself (second pass, `shared/components/SOSButton.tsx`)

The Safety Hub work above is additive. These are defects in the control that already ships to every
rider on the map and every screen of an active ride. They outrank everything in Tier 1.

Worth stating first: the parts that are *well* built. `shared/utils/sosLocation.ts` is genuinely
careful — a hard-bounded fix race, staleness ceiling (2 min) and accuracy ceiling (500 m) so
responders are never handed a km-scale cell-tower fix as an exact position, and a documented
"proceed with no coordinates" fallback. The persistent amber FAILED state that survives dialog
dismissal is the right call. The problems are around that core, not in it.

#### S1. The success dialog claims contacts were notified without checking 🔴

`sos.alert_msg` asserts *"Your location has been shared with Spinr support **and your emergency
contacts**"* on any backend 200. But the backend
(`backend/routes/rides/safety.py:228-253`) returns `contacts_notified`, a per-contact
`contacts[{id,name,notified}]` array, and on SMS failure a `notification_warning`
("Emergency contacts could not be reached — please call them directly").

Nothing reads any of it. `SOSButton`'s `onTrigger` is typed `Promise<void>`
(`SOSButton.tsx:56`) and `rideStore.triggerEmergency` does `await api.post(...); return;`
(`rider-app/store/rideStore.ts:950-951`) — the response is discarded at both layers.

Consequences, all live today:
- Every Twilio SMS can fail and the rider is still told their contacts were notified.
- A rider with **zero** emergency contacts saved gets the same sentence.
- The per-contact `notified` field built for B16 is only consumed by the driver's flag-dark
  `SafetyOverlay`. The rider's shipping SOS never surfaces it.

A safety flow must not claim a notification it cannot confirm. **Effort: S** — the data is already
on the wire; this is plumbing a return type and branching the copy.

#### S2. Nested retry — up to 9 SOS POSTs, no idempotency key 🔴

Both layers retry independently:

| Layer | Attempts | Backoff |
|---|---|---|
| `SOSButton.triggerSOS` (`SOSButton.tsx:176-186`) | 3 | 1 s, 2 s |
| `rideStore.triggerEmergency` (`rideStore.ts:945-961`) | 3 | 1 s, 2 s |

They compose: **up to 9 POSTs to `/rides/{id}/emergency`** per press. The endpoint has no
idempotency key (contrast `claim_stripe_event` on the payments side), and each successful call
inserts a fresh `safety_incidents` row *and* SMS-blasts every emergency contact.

So a request that succeeds server-side but whose response is lost to a flaky connection — the exact
network conditions an SOS is pressed in — produces duplicate incidents in the safety queue and
repeat "URGENT" SMS to the rider's family. Worst case the rider waits roughly **15 s**
(3 s location + five backoff gaps + round trips) before learning it failed and being offered 911.

**Effort: S.** Collapse to one retry ladder and add an idempotency key.

#### S3. On the map, with no active ride, the button cannot do anything 🟠

`POST /rides/{ride_id}/emergency` is the only SOS endpoint — grep confirms no rideless equivalent.
`SOSButton.triggerSOS` only calls `onTrigger` when `rideId` is truthy (`:175`); otherwise it shows
"No Active Ride — call 911 directly."

On the rider home map (`rider-app/app/(tabs)/index.tsx:558`) there is usually **no** active ride.
So in its most-visible placement the button files no incident, sends no SMS, alerts no safety team
— it only suggests 911. This is ACTION_ITEMS **B15(c)**, still open and the sole reason B15's
checkbox is unticked.

Uber's toolkit works regardless of trip state. **Effort: M** — needs a ride-optional SOS endpoint,
which is a real backend design question (what does a safety incident with no ride scope look like in
the admin queue?), not a UI tweak.

#### S4. A dead branch that would auto-dial 911 if it ever ran 🟠

`rider-app/app/(tabs)/index.tsx:559-566`:

```ts
onTrigger={async (rideId, lat, lng) => {
  if (rideId) { await triggerEmergency(rideId, lat, lng); }
  else { Linking.openURL('tel:911'); }   // unreachable
}}
```

The `else` is unreachable — `SOSButton` never invokes `onTrigger` without a `rideId`. Harmless
today. But it is a loaded trap: fixing S3 by making `SOSButton` always call `onTrigger` would
silently activate an **unprompted 911 dial**, violating domain-safety.md's hardest rule
(*"Never auto-dial 911"*). Delete it now, before S3 makes it live. **Effort: XS.**

#### S5. No one-tap 911 anywhere on the map 🟡

911 is only ever offered *after* a 1.2 s hold plus the full network round trip — in the success,
failure, or no-ride dialog. A rider who simply wants emergency services fastest must go through our
alert flow first. The Safety Hub (F2) fixes the discoverability half; the map itself still has no
direct affordance.

#### S6. "I'm OK" tells nobody 🟡

The success dialog's "I'm OK" calls `setTriggered(false)` and nothing else — no backend call, no
all-clear. The incident stays `open` in the safety queue and the contacts who got "URGENT: … call
them or emergency services immediately" never get a follow-up. Correctly, domain-safety.md forbids
*auto*-resolving an SOS — but there is no user-initiated false-alarm signal at all, which is both a
support-load and a trust problem. **Effort: S**, needs a product decision on what contacts are told.

#### S7. On the map it renders as an unlabelled shield 🟡

The `SOS` text only renders at `size="large"` (`SOSButton.tsx:271`), and the map uses
`size="small"` — so it is a bare shield glyph, the same icon the (safe, non-emergency) Safety Hub
would use. `accessibilityLabel` is correct so screen readers are fine; the visual affordance is the
gap.

---

## 5. Recommended sequence

```
Sprint 0 — The shipping SOS button   (defects in a live safety control)
  S1  stop claiming contacts notified without checking
  S2  collapse nested retry + idempotency key
  S4  delete the dead auto-dial-911 branch     ← XS, do before S3 ever lands
  S6  false-alarm / all-clear path
  S7  visible label at size="small"

Sprint 1 — Fix + foundation  (all XS-S, no legal dependency)
  F3  share-token expiry              ← security, live surface
  F1  driver share-link button        ← dead safety control
  F4  correct domain-safety.md        ← stop overstating coverage
  F2  Safety Hub screen (both apps)   ← the container everything else lands in

Sprint 2 — Parity features
  F6  per-area safety authority       ← your idea, lands inside the Hub
  F5  Proof of trip status            ← same regulatory screen family
  F8  real-device QA → flip discreet SOS flag

  S3  rideless SOS (B15(c)) — backend design question, not a UI tweak
  S5  direct 911 affordance on the map

Sprint 3+ — Consent-gated
  F13 emergency-contact OTP/STOP
  F7  auto-share (depends on F13)
  F9  rider discreet overlay
  F12 pickup PIN

Backlog — needs legal
  F10 audio recording
  F11 anomaly detection
```

Sprint 1 alone closes the two genuine defects and produces a screen that *looks* like the Uber
toolkit. Sprint 2 makes it functionally match, plus the authority contact Uber doesn't have.

---

## 6. What this analysis did NOT verify

Stated explicitly rather than left implied:

- **No runtime verification.** Nothing was executed — no backend started, no test suite run, no
  app built. Every finding is from reading source, migrations, and grep. F1 and F3 in particular are
  read-derived and should be reproduced before a fix is written.
- **No production DB inspection.** Whether `service_areas.regulatory_authority` is actually populated
  for the live Regina/Saskatoon rows was not checked — only that migration 223 backfills
  `'SGI'` where `province = 'SK'`. Similarly, how many live `rides` rows carry a
  `shared_trip_token` with a NULL `shared_trip_token_created_at` (F3's blast radius) is unknown.
- **No device/visual check.** This repo has no visual-regression or snapshot tooling at all
  (standing gap, ACTION_ITEMS). Any UI claim here is reasoned from source, not screenshotted.
- **Uber's behaviour is inferred from the screenshot + public knowledge**, not from testing their
  app. "Proof of trip status" and "Follow my ride" semantics are described as commonly documented;
  the exact contents of their screens were not verified.
- **The Calgary bylaw text itself was NOT read.** `calgary.ca` is blocked by this environment's
  network egress proxy, so §3.4/§3.5 rest on secondary sources summarising the City's published
  TNC pages. What is well-corroborated: 311 is the designated rideshare complaint channel, bylaw
  20M2021 exists and was approved 2021-03-22, and the TNDL driver requirements listed (Class 1/2/4,
  60-day PIC, 30-day inspection, 80% training pass). **What is NOT confirmed: whether 20M2021
  obliges the TNC's *app* to display the 311 channel**, versus 311 simply being the City's channel
  that riders use independently. That distinction decides whether §3.4 is a hard compliance
  requirement or a strong product convention — **read the Vehicle for Hire Bylaw 20M2021 PDF before
  citing it as a launch gate in any external or legal document.**
- **No Alberta legal review.** §3.5's Class 5 vs Class 4 conflict is read off published City
  requirements against our own CLAUDE.md text. It has not been reviewed by anyone qualified, and
  Alberta provincial rules (Alberta.ca "ride-for-hire services") were not cross-checked against the
  municipal ones.
- **Effort sizes are relative T-shirt estimates**, not costed against anyone's calendar.
- **No legal review** of the SK recording-consent position in F10 — flagged as needing sign-off
  precisely because it wasn't obtained.
