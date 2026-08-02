# Data Breach Runbook

**Covers:** PII exposure · RLS bypass · wrong-user data leak · credential dump · GPS trace exposure  
**Owner:** Engineering Lead + Legal/Privacy Officer  
**Severity:** SEV-1 by default — escalate to IC immediately  
**Canonical timer:** start the 72-hour PIPEDA clock the moment you have reasonable grounds to believe a breach occurred, **not** when you finish investigating.

Cross-reference: `docs/incident-response.md` (general IR flow) · `docs/runbooks/security-incident.md` §7 (quick triage) · `docs/runbooks/pii-key-rotation.md` (key rotation steps)

---

## 0. Immediate triage (first 15 minutes)

```
[ ] Open a private #breach-YYYY-MM-DD Slack channel — no public channels
[ ] Assign IC, Tech Lead, Legal/Privacy Liaison, Scribe
[ ] Start the 72-hour PIPEDA clock: record exact UTC timestamp here: ___________
[ ] Scope cut: what data? which users? what time window?
[ ] Preserve: export relevant Supabase logs + audit_logs BEFORE any changes
```

**Preserve logs first** — rotating keys or patching before capturing evidence
destroys your ability to assess scope and meet regulatory obligations.

---

## 1. Scope assessment

### 1a. Which data categories were exposed?

| Category | PIPEDA risk | Retention rule |
|---|---|---|
| Phone numbers | High — direct contact | Anonymise on deletion (last-4 only in logs) |
| Rider pickup/dropoff addresses | High — home/work location | Round to city on deletion |
| GPS trace (raw lat/lng) | High | 3-year retention, then delete |
| Driver license / SIN | Critical | Encrypted at rest; never in logs |
| Payment card data | Critical | Stripe-only; Spinr never holds PANs |
| Trip records (time, fare, route) | Medium | 7-year retention for tax/regulatory |
| Names / email | High | Anonymise on deletion |
| OTP / session tokens | Critical — enables impersonation | Invalidate immediately |

### 1a-i. Designated high-sensitivity flow: Data Transfer admin export module

`backend/routes/admin/data_transfer_export.py` (service: `backend/services/data_transfer/entity_export_service.py`)
moves **full-fidelity, unredacted** PII for up to 100 entities per request in a single
ZIP: decrypted government ID / driver license numbers, exact GPS ride history
(not geohashed), and driver identity document bytes. Unlike rider/driver-facing
flows, this data is *unredacted by design* (admin-to-admin environment migration —
see `docs/privacy/2026-07-28-pia-data-transfer-export.md`), so if this module is
implicated in a breach (leaked signed URL, over-broad `bulk_operations` grant,
compromised admin account) treat it as meeting PIPEDA's "real risk of significant
harm" threshold by default rather than re-deriving that judgment mid-incident.

```bash
# Contain: revoke all outstanding signed export URLs and block new ones
UPDATE data_transfer_export_jobs SET expires_at = now() WHERE expires_at > now();
# Add DISABLE_DATA_TRANSFER_EXPORT=true and redeploy (or pull the admin
# module flag granting bulk_operations/data_transfer_pii_export from the
# affected admin account(s) — see §2 "Isolate compromised admin accounts").

# Scope: identify every job whose bundle may have been exposed
SELECT id, requested_by, entity_ids, created_at, expires_at
FROM data_transfer_export_jobs
WHERE created_at BETWEEN '<window_start>' AND '<window_end>';
```

Full sensitivity inventory (per-field breakdown, risk ratings): PIA §3–4.

### 1b. Estimate affected user count

```sql
-- Run via Supabase SQL editor (read-only service role)

-- Rides with raw GPS within the breach window
SELECT COUNT(DISTINCT rider_id)
FROM rides
WHERE created_at BETWEEN '<breach_start>'::timestamptz AND '<breach_end>'::timestamptz;

-- Audit log entries for the affected endpoint / RLS bypass window.
-- audit_logs has no user_id column — entity_id is the affected record's
-- ID (e.g. the rider/driver whose data the action touched); actor_id is
-- who performed the action. For counting affected individuals, filter to
-- the relevant entity_type so entity_id isn't counting unrelated rows
-- (rides, corporate accounts, etc.) that happen to share the action name.
SELECT COUNT(DISTINCT entity_id)
FROM audit_logs
WHERE created_at BETWEEN '<breach_start>'::timestamptz AND '<breach_end>'::timestamptz
  AND action LIKE '%<endpoint>%'
  AND entity_type IN ('users', 'drivers');
```

### 1c. Determine "real risk of significant harm" (PIPEDA threshold)

Notification to the Privacy Commissioner is required when the breach poses
**real risk of significant harm** to individuals. Answer all questions:

- [ ] Does the exposed data include government ID, SIN, or financial account info? → **yes = notify**
- [ ] Does the exposed data include precise location (GPS)? → **yes = likely notify**
- [ ] Does the exposed data enable identity theft or impersonation? → **yes = notify**
- [ ] Is the breach confined to internal staff with no external access? → **no = notify**
- [ ] Could the breach enable physical harm (stalking, home address)? → **yes = notify**

If any box is checked, proceed to §4 (notification). If none, document the
rationale and keep the internal breach record (§5).

---

## 2. Contain

### Stop the bleeding

```bash
# Disable the affected endpoint or route (Railway env var override)
# Add DISABLE_<ENDPOINT>=true and redeploy — faster than a code push.

# Revoke all sessions for affected users (increments token_version)
# Run for each affected user_id:
UPDATE users SET token_version = token_version + 1
WHERE id IN ('<user_id_1>', '<user_id_2>', ...);
# Then delete Redis session keys:
redis-cli DEL "session:<user_id_1>" "session:<user_id_2>"

# If RLS bypass is suspected, disable the anon key immediately:
# Supabase Dashboard → Settings → API → Revoke anon key → generate new one
# Update SUPABASE_ANON_KEY in all app env vars + redeploy

# If JWT_SECRET was exposed:
# Follow docs/runbooks/auth-tokens.md — full secret rotation + mass logout
```

### Isolate compromised admin accounts

```bash
# Force-logout a specific admin
redis-cli DEL "admin_session:<email>"

# Disable admin account (set flag in DB)
UPDATE admin_users SET is_active = false WHERE email = '<email>';
```

---

## 3. Eradicate and recover

1. **Root cause** — identify the exact code path, query, or config that caused the breach. Document in the incident channel.
2. **Fix** — deploy the patch (PR required, no direct push to main).
3. **Verify** — confirm the access path is closed:
   - For RLS bypass: run `docs/runbooks/security-incident.md` §7 RLS test queries.
   - For credential exposure: confirm all derived secrets rotated.
   - For GPS log leak: purge affected log entries from logging backend (Sentry, Railway).
4. **Re-enable** any endpoints or services disabled in §2.

---

## 4. Notification obligations

### 4a. Internal (within 1 hour of scope assessment)

- IC notifies: CEO, CTO, Legal/Privacy Officer, on-call engineering lead.
- Do **not** post to public Slack channels, GitHub issues, or status page until legal reviews messaging.

### 4b. Office of the Privacy Commissioner of Canada (within 72 hours)

**72-hour clock started at:** ___________  
**Deadline:** ___________  

Filing link: <https://www.priv.gc.ca/en/report-a-concern/report-a-privacy-breach-as-an-organization/>

Required information for the form:
- Organization name and contact (Privacy Officer name + email)
- Date breach discovered vs. date breach occurred (if known)
- Description: what data, how many individuals, how it happened
- Steps taken to contain + prevent recurrence
- Whether affected individuals have been notified

If the 72-hour window cannot be met (scope still unknown), file a preliminary report
and update it as facts are confirmed. Late filing is better than no filing.

### 4c. Affected users (as soon as reasonably possible after OPC notification)

Notify via in-app push + email. Legal must approve message before sending.

Template (legal must review):
```
Subject: Important notice about your Spinr account

We are writing to let you know that on [DATE], we discovered that [BRIEF DESCRIPTION
OF WHAT HAPPENED]. The information that may have been affected includes: [LIST].

We have [ACTIONS TAKEN]. We recommend you [USER ACTIONS, e.g. change passwords on
other services if email was exposed].

If you have questions, contact privacy@spinr.ca.

We take your privacy seriously and apologize for any concern this may cause.
```

### 4d. Saskatchewan Government Insurance (SGI) — if driver records exposed

If the breach involves driver license numbers, insurance data, or criminal
record check results, notify SGI's privacy office in parallel with OPC.

---

## 5. Breach record (mandatory — 24 months)

Create an entry in `docs/audit/breach-record.md` (create the file if absent)
with these fields. This record must be retained for 24 months regardless of
whether OPC notification was required.

```markdown
## Breach: YYYY-MM-DD — <one-line description>

| Field | Value |
|---|---|
| Discovery timestamp (UTC) | |
| Breach start (estimated, UTC) | |
| Breach end (confirmed, UTC) | |
| Data categories exposed | |
| Estimated number of individuals | |
| Real-risk determination | yes / no + rationale |
| OPC notified | yes (date+time) / no (rationale) |
| Affected users notified | yes (date) / no (rationale) |
| Root cause | |
| Fix deployed | PR # + date |
| Post-mortem link | |
| IC | |
| Privacy Officer sign-off | |
```

---

## 6. Scrubbing / anonymisation

Data scrubbing must happen **after** the breach record is finalized and
regulatory notifications are sent — not before, as it may destroy evidence.

### PIPEDA deletion vs. SK Transportation Act retention conflict

Some data must be kept under Saskatchewan law even if a user requests
deletion. Never scrub the following:

| Data | Retention minimum | Reason |
|---|---|---|
| Trip records (fare, route summary) | 7 years | Tax / financial audit |
| Driver/vehicle linkage at trip time | 7 years | Regulatory |
| Insurance period transitions | 7 years | Commercial coverage audit |
| GPS at pickup/dropoff (not full route) | 3 years | Regulatory |

For all other PII, execute the anonymisation procedure:

```sql
-- Anonymise a user after deletion request + breach cleanup
-- Run per user_id in a transaction; confirm row count before committing.

BEGIN;

-- Null user identifiers on ride records (keep fare + route for tax)
UPDATE rides
SET rider_id = NULL
WHERE rider_id = '<user_id>';

-- Scrub PII columns from users table
UPDATE users
SET
    phone        = NULL,
    email        = NULL,
    full_name    = 'Deleted User',
    profile_image_url = NULL
WHERE id = '<user_id>';

-- Round GPS on any saved locations
UPDATE user_saved_locations
SET
    lat = ROUND(lat::numeric, 2),
    lng = ROUND(lng::numeric, 2)
WHERE user_id = '<user_id>';

-- Confirm counts before committing
SELECT COUNT(*) FROM rides WHERE rider_id = '<user_id>';   -- expect 0
SELECT COUNT(*) FROM users WHERE id = '<user_id>' AND phone IS NOT NULL;  -- expect 0

COMMIT;
```

After DB scrub: purge any Sentry events, Railway log lines, or analytics
events that contain the affected `user_id`. File tickets with each vendor
if their platform holds the data (check `docs/vendor-register.md`).

---

## 7. Post-mortem (within 5 business days)

Format: `docs/audit/postmortem-YYYY-MM-DD-<slug>.md`

Required sections:
1. Timeline (UTC timestamps)
2. Root cause (5-whys)
3. What went well
4. What went wrong
5. Action items (owner + due date for each)

Share with: IC, Tech Lead, Legal, on-call rotation. Store in `docs/audit/`.

---

## 8. Quick-reference timeline

| Clock | Deadline | Owner |
|---|---|---|
| T+0 | Breach discovered | IC |
| T+15 min | Scope triage complete; PIPEDA clock started | Tech Lead |
| T+1 h | Internal notification (CEO, CTO, Legal) | IC |
| T+24 h | Contain + eradicate complete; breach record draft | Tech Lead |
| T+72 h | OPC notification filed (if required) | Privacy Officer |
| T+ASAP after OPC | Affected users notified | Comms Lead |
| T+5 business days | Post-mortem published | IC |
| T+24 months | Breach record archived (minimum) | Privacy Officer |
