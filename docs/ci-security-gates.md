# CI Security Gates

**Purpose:** Prevent regressions between audits by failing CI when a PR
introduces a known-dangerous pattern. Audits are point-in-time; these gates
are continuous.

**Status:** Specification (gates to be wired into GitHub Actions by `devops`).

---

## Gate Matrix

| Gate | Tool | Applies to | Blocks PR? | Owner |
|---|---|---|---|---|
| G1 — Python SAST | Bandit | `backend/` | CRITICAL/HIGH only | backend |
| G2 — JS SAST | ESLint + eslint-plugin-security | `rider-app/`, `driver-app/`, `admin-dashboard/` | HIGH only | mobile, admin |
| G3 — Pattern SAST | Semgrep (custom rules — see below) | all code | Any match | backend |
| G4 — Dependency audit | `pip-audit`, `yarn audit`, `npm audit` | Python + JS | HIGH severity only | devops |
| G5 — Secret scan | `gitleaks` | entire repo | Any match | devops |
| G6 — License scan | `pip-licenses`, `license-checker` | all | Any non-whitelisted | legal |
| G7 — IaC scan | `checkov` | Terraform / K8s manifests (when added) | HIGH/CRITICAL | infra |
| G8 — Container scan | `trivy` | Docker image | HIGH/CRITICAL CVEs | devops |
| G9 — Money-arithmetic float | Custom grep (existing hook) | `backend/` fare code | Any match | backend |
| G10 — Coverage floor | pytest + jest | per-module thresholds | Coverage drops | engineering |

---

## Semgrep Rules (G3) — Spinr-Specific Patterns

File: `.semgrep/spinr-rules.yml` (to be created).

```yaml
rules:
  # SR-01: MongoDB-style $set wrapper on Supabase update (DV-2)
  - id: spinr-supabase-no-mongo-wrappers
    pattern: |
      $DB.$METHOD($TABLE, {"$set": ...}, ...)
    message: |
      Supabase is not MongoDB. Do not wrap update payloads in {"$set": ...}.
      This silently writes nothing (the key is treated as a column name).
    severity: ERROR
    languages: [python]

  # SR-02: Swallowed DB/auth/payment errors (CLAUDE.md rule)
  - id: spinr-no-warning-on-critical-errors
    patterns:
      - pattern-either:
          - pattern: |
              except DatabaseError:
                  logger.warning(...)
                  ...
          - pattern: |
              except $AUTHERR:
                  logger.warning(...)
                  ...
      - metavariable-regex:
          metavariable: $AUTHERR
          regex: ".*(Auth|Payment|Stripe).*Error"
    message: |
      DB/auth/payment errors must surface loudly. Use logger.error with the
      full exception (for DatabaseError, include e.details['original']).
    severity: ERROR
    languages: [python]

  # SR-03: Float arithmetic in fare/payment code
  - id: spinr-no-float-in-money
    pattern-either:
      - pattern: |
          float($X)
      - pattern: |
          $AMOUNT * 1.0
    paths:
      include:
        - "backend/routes/fares.py"
        - "backend/routes/payments.py"
        - "backend/services/fare_service.py"
    message: "Money arithmetic must use Decimal. See CLAUDE.md."
    severity: ERROR
    languages: [python]

  # SR-04: Ride state transition without guard
  - id: spinr-ride-state-needs-guard
    pattern-either:
      - pattern: |
          $DB.update("rides", {"status": "$STATE", ...}, ...)
    paths:
      exclude:
        - "backend/routes/rides.py"
    message: |
      Ride state changes must go through _require_ride_in_state() in
      routes/rides.py. Direct updates bypass the state-machine guard.
    severity: WARNING
    languages: [python]

  # SR-05: Fixed Stripe idempotency key (DV-4)
  - id: spinr-stripe-idempotency-static
    pattern-regex: |
      idempotency_key\s*=\s*f?"intent-\{.*\}-\{.*\}"
    message: |
      Static idempotency key "intent-{user}-{amount}" collides on same-amount
      retries. Use a UUID provided by the client per request.
    severity: ERROR
    languages: [python]

  # SR-06: OTP brute-force path — lockout check required
  - id: spinr-otp-requires-lockout-check
    pattern: |
      await verify_otp($PHONE, $CODE)
    pattern-not-inside: |
      if await otp_lockout_active($PHONE): ...
      ...
    message: "OTP verify must be gated by otp_lockout_active() check."
    severity: WARNING
    languages: [python]

  # SR-07: New background loop must use idempotency / atomic claim
  - id: spinr-background-loop-needs-idempotency
    pattern: |
      while True:
          ...
          await $ACTION(...)
    paths:
      include:
        - "backend/core/lifespan.py"
    message: |
      New background loops run on every replica. Verify action uses atomic
      DB claim, idempotency key, or reminder_sent flag.
    severity: INFO
    languages: [python]
```

---

## Coverage Floor (G10)

```yaml
# backend/pytest.ini
--cov-fail-under=50         # current floor; raise 5 pp/sprint

# driver-app/jest.config.js
coverageThreshold:
  global:
    branches: 40
    functions: 50
    lines: 50
    statements: 50

# rider-app/jest.config.js
coverageThreshold:
  global:
    branches: 40
    functions: 50
    lines: 50
    statements: 50
```

**Rule:** A PR may not *lower* the coverage percentage. If your change
drops coverage by more than 0.5 pp, add tests.

---

## PR-Blocking Severity Mapping

| CI Output | Action |
|---|---|
| `CRITICAL` finding from any gate | Block merge; auto-request review from `security` team |
| `HIGH` finding | Block merge; bypassable only with explicit `risk-accepted` label + owner comment |
| `MEDIUM` finding | Warning comment; does not block |
| `LOW` / informational | Surface as review comment only |

---

## Scheduled Scans (not tied to PR)

| Scan | Cadence | Target | Output |
|---|---|---|---|
| Full dependency audit | Daily 02:00 UTC | backend + all frontends | Posts to `#security-alerts` |
| Container scan (prod image) | Daily | Latest tag | GitHub Issue if HIGH/CRITICAL |
| Gitleaks full-history | Weekly | Main branch | Manual review of hits |
| Supabase RLS-policy runtime check | Weekly | Staging DB | Report emailed to backend + compliance |
| License-audit | Weekly | All `package.json` / `requirements.txt` | Issue if new non-whitelisted license |

---

## Implementation Checklist

- [ ] Create `.semgrep/spinr-rules.yml` with rules above
- [ ] Add `.github/workflows/security-gates.yml` running G1–G8 on every PR
- [ ] Add `gitleaks` pre-push hook
- [ ] Wire Dependabot (or Renovate) for Python + JS ecosystems
- [ ] Document license whitelist in `docs/license-whitelist.md`
- [ ] Add `risk-accepted` GitHub label with required-reviewer rule
- [ ] Add `#security-alerts` Slack channel webhook
- [ ] Run G1–G5 against main branch at current HEAD to establish baseline
- [ ] File remediation items for all current HIGH/CRITICAL findings

---

## Relationship to Audit Framework

These gates are **complementary** to the 22-dimension audit, not a replacement.

- **Audit** = comprehensive, point-in-time, human-judgement-involved, ~55–75 hours per module.
- **CI gates** = narrow, automated, continuous, seconds per PR.

An audit defines the *what*; CI gates encode the *how* so the `what` doesn't
regress between audits.

Last updated: 2026-04-24
