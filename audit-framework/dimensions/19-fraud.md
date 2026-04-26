# Dimension 19 — Fraud Detection

**Question:** Can a rider, driver, or outsider abuse the platform for financial gain, account takeover, or service denial — and would we notice?

---

## Checklist

### Account / identity abuse
- [ ] Device fingerprinting on signup (install id, device model, carrier, timezone)
- [ ] SIM-swap detection — carrier-line-age check via Twilio / carrier API if available
- [ ] Velocity rules: N sign-ups per device / IP / day → review queue
- [ ] "Impossible travel" detection on driver location (GPS jumps > 300 km/h)
- [ ] Refer-to-self detection: same device / payment method / IP in referrer + referee
- [ ] Burner-number detection (known disposable-SMS carriers blocked or flagged)

### Payment / wallet abuse
- [ ] Stripe Radar enabled with default rules + Spinr-specific rules for Canadian card BINs
- [ ] Chargeback workflow — disputes auto-pull evidence (ride trace, GPS breadcrumbs, driver rating, signed fare)
- [ ] Wallet top-up velocity limits (per day / per week) + KYC uplift above threshold
- [ ] Prepaid-card abuse detection — promo-bomb with prepaid cards is a classic
- [ ] Refund pattern detection: same rider, frequent refund requests

### Promo / referral abuse
- [ ] Promo codes require fresh PII + payment method — not just a new account
- [ ] First-ride discount caps per device / per household (shared-address heuristic)
- [ ] Referral bonus only pays after referee's first completed paid ride (not signup)
- [ ] Stacked-promo prevention at fare settlement

### Ratings / ride-abuse
- [ ] Coordinated rating attack detection (N 1-star ratings on same driver from new accounts in short window)
- [ ] Cancel-then-rebook fee-avoidance detection
- [ ] Driver "gaming" detection: short trips to same destination, accept-reject churn, inflating waiting time
- [ ] Fake-ride detection: no GPS movement during "trip"

### SOS / safety abuse
- [ ] Abuse of SOS button (prank calls) tracked but rate-limited so real SOS still works
- [ ] Driver-to-rider harassment reporting + freeze on complained account pending review

### Sanctions / high-risk
- [ ] OFAC / UN / Canadian sanctions screening on corporate-account signups
- [ ] PEP (Politically Exposed Person) flag on corporate KYC

### Fraud operations
- [ ] Fraud analyst role exists in admin panel with review queues
- [ ] Manual "freeze account" action + audit log + appeal path
- [ ] Fraud dashboard: per-day counts of signups flagged, chargebacks, rating-attacks, wallet anomalies
- [ ] Model/heuristic retrain cadence; false-positive review

---

## Common Findings

- **No velocity limits** — same device can create 50 riders in an hour, each with a free-ride promo.
- **Self-referral closes trivially** — referrer and referee share payment method / device id.
- **No Stripe Radar rules** — default fraud model is left at generic; Canadian BIN rules not set.
- **Chargeback disputes are manual** — no auto-bundled evidence pack; lose most disputes.
- **Rating-attack impossible to detect** because we don't store rater identity in a way that correlates across drivers.

## How to Test

```bash
# Promo code abuse controls
grep -rn "promo.*used\|max_uses\|eligible_for_first_ride\|first_ride_discount" \
  backend/routes/promotions.py backend/services/ 2>/dev/null

# Device fingerprint fields on signup
grep -rn "device_id\|install_id\|fingerprint" \
  backend/routes/auth.py backend/schemas.py shared/ 2>/dev/null

# Impossible-travel / GPS plausibility
grep -rn "max_speed\|plausible\|teleport\|300.*kmh" \
  backend/validators.py backend/routes/drivers.py

# Stripe Radar configuration (set out-of-band; confirm runbook exists)
grep -rn "radar\|risk_score\|cvc_check" backend/routes/payments.py
```

## Regulatory tags
`PCI-DSS` (Radar + chargeback evidence) · `AML` (velocity + KYC + sanctions) · `PIPEDA` (fraud data is still personal data — retention rules apply) · `SK-HRC` (complaint + freeze workflow)
