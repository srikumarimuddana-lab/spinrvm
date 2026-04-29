# Supabase Data Residency Attestation — Rider Phase E 22-2

**Owner:** Privacy Officer / Legal  
**Due:** Q2 2026 (before public launch)  
**Regulation:** PIPEDA s.4.1.3 — data transferred across borders must remain subject to comparable protection  
**Risk if missed:** OPC complaint; DPA violation; engineering startup check logs ERROR on every production boot

---

## Background

Spinr's primary datastore is a Supabase project. Under PIPEDA, all personal data must reside in Canada or be transferred under a documented adequacy / contractual mechanism. The Supabase project must be in region **`ca-central-1`** (Supabase Cloud Canada — hosted on AWS ca-central-1, Montréal).

Engineering startup check added (lifespan.py): if `SUPABASE_REGION` env var is absent or non-CA in production, the backend logs a PIPEDA ERROR on every boot. This is intentional friction to ensure the attestation is completed before launch.

---

## Steps to Complete

### 1. Verify the Supabase project region

1. Log in to [app.supabase.com](https://app.supabase.com) with the Spinr organization account.
2. Open the Spinr project → **Settings → General**.
3. Confirm **Region** shows **`ca-central-1`** (Canada / ca-central-1).
4. Take a screenshot of the Settings page showing the region.

### 2. Obtain written DPA from Supabase

1. Contact Supabase Sales at <dpa@supabase.io> (or via the in-dashboard chat).
2. Request a signed **Data Processing Agreement (DPA)** for the Spinr organization.
3. Reference: [Supabase Privacy Policy](https://supabase.com/privacy) and their DPA template.
4. Confirm the DPA covers Canadian data-residency requirements.

### 3. File the attestation

1. Save the signed DPA PDF as `reports/legal/dpa-supabase-2026.pdf`.
2. Add the screenshot from step 1 as `reports/legal/supabase-region-screenshot-2026.png`.
3. Update `docs/vendor-register.md`: change Supabase `Effective date` from `_TBD — file DPA_` to the DPA signing date.

### 4. Set the environment variable

In the Railway backend environment:
```
SUPABASE_REGION=ca-central-1
```

This silences the startup PIPEDA error log and confirms the region is documented.

### 5. Verify the checklist at next privacy review

Add to the annual privacy audit (next: 2027-04-27): confirm Supabase region has not changed.

---

## Contact

- **Supabase DPA:** <dpa@supabase.io>
- **Internal escalation:** Privacy Officer → Legal
- **Engineering questions:** Backend team (`backend/core/lifespan.py:init_database`)

---

## Status

| Step | Owner | Status | Date |
|------|-------|--------|------|
| Verify region in Supabase dashboard | Privacy Officer | ⬜ Open | — |
| Obtain signed DPA from Supabase | Legal | ⬜ Open | — |
| File DPA as `reports/legal/dpa-supabase-2026.pdf` | Legal | ⬜ Open | — |
| Update `docs/vendor-register.md` effective date | Legal | ⬜ Open | — |
| Set `SUPABASE_REGION=ca-central-1` in Railway | Engineering | ⬜ Open | — |
