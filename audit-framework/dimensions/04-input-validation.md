# Dimension 04 — Input Validation & Sanitisation

**Question:** Can bad data get into the system? Can an attacker inject malicious input?

---

## Checklist

### Phone & Identity Fields
- [ ] Phone validated as E.164 format (`+1XXXXXXXXXX` for Canada)
- [ ] Country code enforced to `+1` (Canada/US only — not open to all countries)
- [ ] Email validated against RFC 5322 pattern (not just "contains @")
- [ ] Name fields: max length enforced, no HTML/script tags, minimum 2 chars

### GPS & Location Fields
- [ ] Latitude range: −90 to +90 (reject NaN, null, Infinity)
- [ ] Longitude range: −180 to +180
- [ ] Null island (0,0) rejected with 400 — not just logged
- [ ] Coordinate precision: not more than 8 decimal places (avoids DB overflow)
- [ ] Consider: reject coordinates outside Canada service area (Saskatchewan bounding box)

### Monetary Fields
- [ ] All monetary amounts use `Decimal` not `float` (float causes rounding errors)
- [ ] Minimum enforced (≥ $0.00 — no negative amounts)
- [ ] Maximum enforced (reject absurdly large fares — e.g. > $10,000)
- [ ] Currency code validated (CAD only for Spinr)
- [ ] Amount cross-checked against the ride fare record on the server — not just client-supplied

### String Sanitisation
- [ ] HTML tags stripped from all string inputs (use `bleach`, not naive regex)
- [ ] SQL injection: Pydantic + parameterised queries prevent injection (verify ORM usage)
- [ ] XSS: check if any user input is reflected in API responses without escaping
- [ ] Max length enforced on all string fields (no unbounded text input to DB)
- [ ] Minimum length enforced where appropriate (address ≥ 10 chars, not 3)

### File Uploads
- [ ] Magic byte validation — not just file extension check
- [ ] MIME type allowlist (JPEG: `FF D8 FF`, PNG: `89 50 4E 47`, PDF: `25 50 44 46`, WebP: `52 49 46 46...57 45 42 50`)
- [ ] WebP: check bytes 8–12 are `WEBP` (not just `RIFF` — shared with AVI/WAV)
- [ ] File size checked before reading into memory (Content-Length header check)
- [ ] Max file size enforced (Spinr standard: 10MB for documents)
- [ ] Original filename not returned in API response (PII leakage)

### Schema-Level Validation
- [ ] All Pydantic models have `Field(...)` constraints — no unconstrained `str`
- [ ] Optional fields that should be required are marked required
- [ ] Enum fields use Python `Enum` — not raw string comparison
- [ ] Numeric IDs validated as UUID format where applicable

### Client-Side Validation
- [ ] Client-side rules match server-side rules exactly
- [ ] Server-side validation is authoritative — client validation is UX only
- [ ] Direct API calls (bypassing the app form) still get server-side validation

---

## Severity Guide

| Finding | Severity |
|---|---|
| No server-side validation — client-supplied value trusted blindly | HIGH |
| SQL injection possible | CRITICAL |
| XSS — user input reflected without escaping | HIGH |
| File uploaded entirely into memory before size check (DoS) | HIGH |
| Monetary field is `float` type | MEDIUM |
| Null island GPS not rejected | MEDIUM |
| Phone accepts any country code | HIGH |
| Magic byte not validated (only extension) | MEDIUM |
| Max length missing from string field | MEDIUM |
