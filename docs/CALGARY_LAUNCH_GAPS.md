# Calgary Launch — Gap Register

**Created:** 2026-08-05 · **Verified against:** `main` @ `d9cf476` + branch `claude/code-review-calgary-saskatoon-pmrlk2`
**Status:** register only — no code changed by this document.

Companion to `docs/runbooks/saskatoon-launch.md`. That runbook is the Saskatoon gate; this is the
Alberta delta. **Calgary inherits every open Saskatoon blocker in addition to everything here.**

Each gap is numbered, scoped, and marked with what closes it: **CODE**, **CONFIG** (admin/DB, no
deploy), **OPS** (a human with an account), or **LEGAL/EXTERNAL** (outside the company).

---

## 0. The requirement that reshapes everything

**Alberta requires a Class 1, 2 or 4 licence to carry passengers for compensation. A Class 5 is
not sufficient.** Upgrading requires a knowledge test, a road test, and a medical fitness check.

Every eligibility surface in this product assumes SK's Class 5 + SGI endorsement model. That is
not a copy problem — it changes driver acquisition economics and timeline. A Saskatoon driver
already holds their Class 5; a Calgary driver needs weeks of process and real out-of-pocket cost
before their first fare.

**Consequence: driver recruitment is the Calgary critical path, not engineering.** Budget 6–10
weeks per cohort. The code work below is ~2–3 weeks.

> Everything in §1 is desk research from City of Calgary and Government of Alberta sources.
> **Confirm with counsel before designing around it.** Municipal bylaws change.

---

## 1. Regulatory requirements (LEGAL/EXTERNAL — start these first, they are pure lead time)

### 1.1 — Company: City of Calgary TNC business licence
Required before advertising, offering, *or* operating. Governed by **Vehicle for Hire Bylaw
20M2021**. Apply through the City's Vehicle for Hire online portal.
*(Note: Bylaw 6M2007 is the older Livery Transport Bylaw — 20M2021 supersedes it for TNCs.)*

### 1.2 — Company: Alberta TNC insurance
A motor-vehicle liability or transportation-network automobile policy meeting the Alberta
regulation's minimum coverage. **SGI does not operate in Alberta** — this is a private-carrier
relationship with no relationship to the SK fleet policy.

### 1.3 — Company: $0.10 per-trip accessibility levy
Mandatory on every taxi and TNC trip in Calgary since 2019-01-01, funding the Accessible Taxi
Incentive Program. Must be collected and remitted. **No code needed** — see gap 3.1.

### 1.4 — Company: app-only booking and payment
No street hail, no phone/text/web booking, no cash, no out-of-app electronic payment.
**Already satisfied** — see §3.

### 1.5 — Driver: Alberta Class 1, 2 or 4 licence
Knowledge test + road test + medical fitness check to upgrade from Class 5.

### 1.6 — Driver: Transportation Network Driver's Licence (TNDL)
City-issued, **$185**.

### 1.7 — Driver: Livery Driver Training Program
Required for the TNDL.

### 1.8 — Driver: Police Information Check + vulnerable sector
Dated within **60 days**, completed specifically for TNDL purpose.

### 1.9 — Driver: annual ELVIS inspection
Enhanced Livery Vehicle Inspection Standards. Only at City-licensed, AMVIC-approved garages.

### 1.10 — Driver: commercial registration + 1-55 plates
Plate class **1-55 (Ride-for-Hire Services)**.

### 1.11 — OPEN QUESTION: WAV obligation in Calgary
`backend/services/dispatch_service.py:203` justifies the WAV dispatch rule with *"Saskatchewan
Transportation Act s.22"*. The **logic** is province-agnostic and fine; the **justification** is
not. Confirm Calgary's equivalent obligation before relying on the same behaviour.

---

## 2. Blocking code gaps

Ordered by severity. Each is a real defect for Alberta, not a nicety.

### 2.1 🔴 Insurance billing bills Alberta kilometres to SGI — **CODE**
`backend/routes/admin/compliance.py:752-753` hardcodes `_SGI_RATE_PER_KM = 0.11` and
`_KNIGHT_ARCHER_RATE_PER_KM = 0.011` as module constants. `_insurance_billing_detail_rows`
(`:771+`) fetches **all** drivers' Period 2/3 kilometres with **no province or service-area
filter**. Endpoints at `:924-938` (SGI) and `:941-965` (Knight Archer).

The day Calgary goes live, Alberta drivers' insured kilometres flow into the SGI invoice
reconciliation — a financial and regulatory reporting error against an insurer that does not
operate in Alberta. Silent, with no error surface.

**Fix:** filter by insurer province / service area. Land this *before* any AB driver exists.

### 2.2 🔴 Self-signup never sets `regulatory_authority` → Calgary drivers land on SGI forms — **CODE**
`backend/routes/admin/sgi_forms.py:45-57` hard-blocks non-SGI drivers, but the filter is
`d.get("regulatory_authority") and d[...] != _SGI_AUTHORITY` — **NULL passes as in-scope**.

And nothing in the organic signup path ever sets that field. Only CSV import
(`services/driver_import_service.py:398-417`) and manual admin edit
(`routes/admin/drivers.py:1352-1355`) write it. So **every Calgary driver who self-signs-up
through the app gets NULL and is silently SGI-eligible.**

Related: the admin UI infers `regulatory_authority = "SGI"` from a non-null `sgi_approved`
(`admin-dashboard/src/app/dashboard/drivers/page.tsx:465,1225`).

**Fix:** set the field from the service area at driver creation, and make the SGI-form scope
fail-closed on NULL.

### 2.3 🔴 Saskatchewan FAQs are served globally, including to Calgary drivers — **CODE (data migration)**
`backend/migrations/212_seed_saskatchewan_driver_faqs.sql:19-20` inserts every SK FAQ with **no
`service_area_ids`** — and per migration 229, NULL/empty means *global*.

So every SGI / Class 5 / SK-endorsement answer is served to Calgary drivers, including through
the AI assistant (`backend/ai/tools_support.py::search_faqs`, `backend/routes/faqs.py`,
`driver-app/app/driver/faq.tsx:62-63`).

Telling a Calgary driver they need a Class 5 and an SGI endorsement is **actively wrong guidance
on the licensing question that costs them the most time and money** (see §0).

**Fix:** data migration tagging the 212 rows to SK service areas; then author AB FAQs.

### 2.4 🔴 `service_areas.timezone` cannot be set through the admin API — **CODE**
Column defaults to `'America/Regina'` (migration 105, `NOT NULL DEFAULT`). No `timezone` field on
`ServiceAreaCreateRequest` (`routes/admin/service_areas.py:140-189`) or `ServiceAreaUpdateRequest`
(`:192-234`); absent from the update allowlist (`:557-607`); never written by the create insert
(`:422-460`); absent from the dashboard create payload
(`admin-dashboard/.../service-areas/page.tsx:142-158`).

Saskatchewan is UTC−6 year-round. **Alberta is Mountain with DST.**

A `calgary` preset already exists at `service-areas/page.tsx:27` — so an admin can create Calgary
**in one click** and have it silently land on Regina time.

The irony that makes this blocking rather than cosmetic: `routes/drivers/earnings.py:202-208`,
`routes/rides/queries.py:220-231` and `utils/spinr_pass.py:64-76` **already resolve the per-area
timezone correctly**. They are correct code reading a column no admin can set.

**Fix:** add `timezone` to the create/update models, the create insert, and the allowlist; set
`America/Edmonton` on the Calgary/Edmonton presets.

### 2.5 🔴 Driver earnings statements cut on Saskatchewan midnight — **CODE**
`backend/utils/driver_statement.py:40` — `STATEMENT_TZ = ZoneInfo("America/Regina")`, used at
`:98-99` to cut every statement period. The docstring at `:22-24` says per-area timezones are
*deliberately* ignored. `utils/driver_statement_pdf.py:180` prints "America/Regina time" to the
driver.

**This is a money artifact.** An Alberta driver's weekly/monthly earnings boundaries are an hour
off, on a document they may hand to an accountant.

Same class, same fix needed: `utils/driver_activity.py:30,40-45` + `routes/admin/drivers.py:2989`
(daily activity / insurance-km report), `utils/quest_tracker.py:23,33` (**quests pay money**),
`utils/driver_onboarding_reminder_rules.py:13`, `routes/drivers/subscriptions.py:141`,
`services/dispatch_service.py:424`.

**Fix:** resolve from the driver's service-area timezone. Depends on 2.4.

### 2.6 🔴 Alberta drivers charged Saskatchewan PST on Spinr Pass — **CODE**
`subscription_tax_config` column default is `{"province":"SK","gst_rate":5.0,"pst_rate":6.0,…}`
(migration 185:22-24), and `routes/drivers/subscriptions.py:248,257` falls back to
`province="SK"` / `pst_rate=6`. **Alberta has no PST.**

There *is* a fix-up path — `PUT /service-areas/{area_id}/subscription-tax`
(`routes/admin/subscriptions.py:461-506`) with UI on the Subscriptions page. It is still blocking
because `subscription_tax_config` is **not written at area-create time**
(`routes/admin/service_areas.py:422-460`), so Calgary silently inherits the SK default and starts
billing 6% PST until someone remembers to visit a different admin page. The fix-up endpoint's own
model also defaults `province="SK", pst_rate=6.0` (`admin/subscriptions.py:459-461`).

**Fix:** derive from `province` at create time; drop the SK fallbacks.

### 2.7 🔴 GST/PST remittance blends provinces into one filing — **CODE**
`backend/routes/admin/compliance.py:218` fetches
`id,ride_completed_at,tax_breakdown,total_fare` — **no `service_area_id`** — and `:225-226` groups
by `month_key` only. Rows at `:258-268` carry month alone.

With AB and SK both live, you cannot produce a per-province CRA / SK-Finance filing.

**Fix:** add `service_area_id` to the fetch and a province dimension to the grouping.

### 2.8 🔴 Driver statement asserts PST to Alberta drivers — **CODE**
`backend/utils/driver_statement_pdf.py:178` prints *"GST/PST collected on fares is included in
your earnings."* There is no PST in Alberta. Misleading copy on a money artifact.

**Fix:** render from the ride's actual `tax_breakdown` labels.

### 2.9 🔴 Vehicle-age rule hardcoded to Saskatchewan — **CODE**
`driver-app/app/become-driver.tsx:376` — *"Vehicle must be 9 years old or newer."* Also `:543`
*"9 years old or newer (2017+)"* — a hardcoded year, **already stale in 2026** — and `:540`
*"compliance with Saskatchewan regulations."* Plus
`admin-dashboard/src/app/register/driver/page.tsx:214` *"(Saskatchewan regulation)."*

Client-side, not per-area. Shows Calgary applicants the wrong rule.

**Fix:** drive from the service area.

### 2.10 🟠 Alberta not seeded in `provinces` — **CONFIG (migration)**
`migrations/259_provinces_reference_table.sql:71-72` seeds **only SK**. `service_areas.province_code`
is a real FK (migration 260) — an `AB` row must exist first.
`utils/report_branding.py:506-523` reads province letterhead from this table, so an unseeded AB
gives blank regulator letterhead on reports.

**Fix:** seed `('AB','Alberta',<authority>,'America/Edmonton')`.

### 2.11 🟠 Driver bulk-import is hard-gated to Saskatoon — **CODE**
`services/driver_import_service.py:354` raises *"Saskatoon service area was not found"*; `:509-516`
rejects rows not scoped to `"saskatoon"`; `:605` defaults city to Saskatoon; `:738` prefixes
storage keys `saskatoon-import/`. `routes/admin/driver_import.py:86` defaults the area name to
`"Saskatoon"`. Dashboard at `drivers/import/page.tsx:167,313,316`.

Blocking **only if** you bulk-onboard Calgary drivers by CSV — which, given the licensing lead
time in §0, you probably will.

---

## 3. Already correct — do not rebuild these

Verified in code. Worth stating so the Calgary work stays scoped.

| Area | Evidence |
|---|---|
| **Ride-fare tax is fully per-area** and the admin create defaults (`gst_enabled=true/5.0, pst_enabled=false`) are **already Alberta-correct** | `backend/features.py:936-945`; `routes/admin/service_areas.py:162` |
| **The $0.10 levy needs no code** — `area_fees` supports per-area `calc_mode: flat`, itemised on both receipt formats | `features.py:636`; `utils/email_receipt.py:119`; `utils/receipt_pdf.py:68` |
| **No cash payment path exists** — methods are `card` / `wallet` / `company_allowance`. Bylaw 20M2021's in-app-payment rule is satisfied by construction | `backend/schemas.py:546,605`; `routes/rides/payments.py:388-397` |
| **Geofencing** enforces pickup, dropoff *and* every stop against active polygons | `routes/rides/booking.py:456-500` |
| **`required_documents` is per-service-area** — Calgary's TNDL, ELVIS cert, 1-55 registration and Class 4 are configuration | `backend/documents.py:404-449`; `onboarding_status.py:144-164` |
| **FAQs are service-area scoped** (the column exists; see 2.3 for the data problem) | `migrations/229_faqs_service_area.sql:38` |
| **`license_class` is free-text** — nothing rejects a Class 4 | `services/data_transfer/sgi_field_maps.py:72` |
| **Corporate tax regions already include AB** | `backend/validators.py:571-580` |
| **Per-area timezone resolution already implemented** in earnings, ride queries, Spinr Pass | `routes/drivers/earnings.py:202-208`; `routes/rides/queries.py:220-231`; `utils/spinr_pass.py:64-76` |

---

## 4. Cosmetic / misleading — fix before launch, not blocking

| # | Gap | Location |
|---|---|---|
| 4.1 | Guest notification display timezone hardcoded Regina | `services/guest_notification_service.py:48` |
| 4.2 | Admin driver-activity renders in Regina | `admin-dashboard/.../driver-activity.tsx:7,25,52,74` |
| 4.3 | Map/GPS fallback centres are Saskatoon (8 files) — a Calgary rider who denies location lands in Saskatoon | `rider-app/constants/geo.ts:5-6`; `pick-on-map.tsx:62,75`; `confirm-pickup.tsx:51-52`; `heat-map.tsx:79`; `geofence-map.tsx:120`; `track/[rideId]/page.tsx:15`; `useAdminLocation.ts:19-20`; `driver-app/lib/androidAuto/carSurface.tsx:38` |
| 4.4 | Terms say governed by **Ontario** law — wrong for every province | `driver-app/app/legal.tsx:58` |
| 4.5 | Hardcoded SK service-area fallback lists with **fake non-UUID ids** (`'saskatoon'`, `'regina'`) | `driver-app/app/profile-setup.tsx:107-108`; `driver-app/app/become-driver.tsx:81-82`; `rider-app/app/become-driver.tsx:60,339` |
| 4.6 | "Fare Lock (SK Regulation)" labels a global setting as SK-specific; SK placeholder phone/address | `admin-dashboard/.../settings/page.tsx:999-1014,1075,1100` |
| 4.7 | Admin infers `regulatory_authority = "SGI"` from `sgi_approved` | `admin-dashboard/.../drivers/page.tsx:465,1225` |
| 4.8 | Phone-validation copy says "can't receive a Saskatchewan ride" (regex is NANP-wide, so 403/587/825 work) | `backend/validators.py:35-37,66-69` |
| 4.9 | AI prompts describe Spinr as "Saskatchewan-first" | `backend/ai/prompts.py:12,178`; `ai/support_assistant.py:56` |
| 4.10 | Non-SK/AB provinces send `pst_rate` without `pst_enabled` → silently collect no PST in BC/MB | `admin-dashboard/.../service-areas/page.tsx:155` |
| 4.11 | Surge tiers are global module constants, commented as "tuned for a mid-size city (Saskatchewan-scale)". Calgary is ~5× Saskatoon — will mis-price, nothing breaks | `utils/surge_engine.py:52-64` |

---

## 5. Sequencing

| Phase | Work | Duration |
|---|---|---|
| **Now** | Start 1.1 (TNC licence) and 1.2 (insurance). Pure lead time, no code dependency. | Start today |
| **Saskatoon first** | Do not launch Calgary before Saskatoon is live and stable. Launching a first-ever public market and a new province at once doubles the regulatory surface at the point of least operational experience. | — |
| **Code** | 2.1 → 2.11, in that order. 2.1 and 2.2 before any AB driver exists. | ~2–3 weeks |
| **Driver pipeline** | Cohort 1: licence upgrade → training → TNDL → ELVIS → plates | **6–10 weeks, the critical path** |
| **Config** | Seed AB, create the area from the preset, set `vehicle_pricing`, `required_documents`, the $0.10 `area_fee`, tax config, timezone | Days |
| **Launch** | An Alberta launch runbook mirroring `saskatoon-launch.md` | — |

**Realistic distance: 3–4 months**, dominated by 1.1 and the driver pipeline.

---

## 6. Decisions needed

1. **Confirm §1 with counsel** — especially 1.5, since the licence class reshapes acquisition.
2. **Is Calgary contingent on Saskatoon launching first?** Strong recommendation: yes.
3. **Will Spinr subsidize driver entry cost** (Class 4 upgrade, $185 TNDL, training)? This is the
   main lever on Calgary supply and it is a business call.
4. **Per-vehicle-type pricing (B8)** — still blocked on pricing authority, now for two markets.

---

## 7. Verification

- `SELECT * FROM provinces WHERE code='AB';` → row with `default_timezone='America/Edmonton'`.
- Create a Calgary area via the admin UI, then
  `SELECT timezone, subscription_tax_config FROM service_areas WHERE city='Calgary';` — Regina or
  `pst_rate: 6` means 2.4/2.6 are not closed.
- Buy a Spinr Pass against Calgary, read `subscription_payments` — no PST line.
- Book a Calgary test ride, read the receipt — GST 5% only, **plus a $0.10 accessibility fee as
  its own disclosed line item**.
- Run the GST/PST remittance export with both areas live — AB and SK must be separable.
- Generate an earnings statement for a Calgary driver — period boundary in Mountain time, and no
  PST assertion in the footer.
- Run the SGI form generator with a Calgary driver present — they must be **excluded**, not
  silently included via NULL.
- Run the insurance billing report with a Calgary driver present — their kilometres must **not**
  appear on the SGI invoice.
