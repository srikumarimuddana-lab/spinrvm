# Security Policy

## Reporting a Vulnerability

If you discover a security issue in Spinr, please **do not** open a public GitHub
issue. Instead, email **security@spinr.ca** (or open a private advisory at
`Security > Advisories > Report a vulnerability` on this repository).

Include:
- A description of the issue and its potential impact
- Steps to reproduce (proof-of-concept code welcome, but not required)
- Affected surface (rider-app, driver-app, backend, admin panel)
- Your preferred name/handle for credit (or request anonymity)

We will acknowledge receipt within **3 business days** and provide an initial
assessment within **10 business days**.

## Safe Harbor

We will not pursue civil or criminal legal action against researchers who:
- Act in good faith and report vulnerabilities promptly
- Do not access, modify, or destroy data beyond what is necessary to demonstrate the issue
- Do not degrade service availability (no DoS, no spam, no social engineering of staff)
- Do not publicly disclose the issue before we have had a reasonable opportunity to remediate (90 days default, extensions negotiable)
- Do not violate the privacy of Spinr riders or drivers

This safe harbor applies to Spinr-operated systems only. It does not authorize
testing against our vendors (Supabase, Stripe, Firebase, Twilio, Google, etc.);
test those through their respective disclosure programs.

## In-Scope Surfaces

- `*.spinr.ca` production endpoints
- `com.spinr.user` (rider iOS/Android app)
- `com.spinr.driver` (driver iOS/Android app)
- Spinr admin dashboard (if URL shared with you)

## Out of Scope

- Physical attacks against Spinr staff, offices, drivers, or riders
- Social engineering of Spinr employees or support agents
- Testing that impacts other users (no brute-force against live accounts)
- Missing security headers without a demonstrated impact
- Vulnerabilities in third-party services (Stripe, Firebase, Supabase, etc.) — report those upstream
- Self-XSS, clickjacking without meaningful impact, rate-limit bypasses with LOW impact

## Rewards

Spinr does not operate a paid bug bounty program at this time. Qualifying reports
receive acknowledgement in the project's `SECURITY-HALL-OF-FAME.md` (with your
consent) and, for HIGH/CRITICAL issues, Spinr-branded swag.

## Contact

- **Email**: security@spinr.ca
- **PGP**: (to be published at /.well-known/security-pgp-key.asc)
- **Response SLA**: 3 business days acknowledgement · 10 business days assessment

---

Last updated: 2026-04-24 · Policy version: 1.0
