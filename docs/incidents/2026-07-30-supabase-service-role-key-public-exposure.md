# P0 Incident — `SUPABASE_SERVICE_ROLE_KEY` exposed in a public repository

**Detected:** 2026-07-30, while verifying the secret-scanning CI gate (T5)
**Status:** key rotation owner-actioned and confirmed in progress; scope assessment **incomplete** — blocked on access only the owner has
**Runbook:** `docs/runbooks/data-breach.md` · **Classification:** P0
**Related:** `docs/change-log/2026-07-30-secret-scanning-gate-was-vacuous.md`

> ## The headline
>
> A **live Supabase `service_role` key** — which bypasses all Row Level Security —
> was committed to **`backend/.env.example` in a PUBLIC GitHub repository**. It was
> world-readable in git history for roughly **3.5 months**, including about **5 days
> directly in the browsable file tree of `main`**. It was never rotated until today,
> so it was valid for that entire period and remained valid at the moment of
> discovery.
>
> This is not "a credential someone with repository access might have seen." It was
> public.

---

## 0. What was exposed

| Property | Value |
|---|---|
| Credential | `SUPABASE_SERVICE_ROLE_KEY` |
| Claims | `iss: supabase`, `role: service_role`, real 20-char project `ref` |
| Signature | HS256, 43 chars — a genuine signed token, not a placeholder |
| Issued | 2026-02-11 |
| Expires | **2036-02-12** (10-year validity) |
| Valid at discovery? | **Yes** |
| Privilege | **Bypasses all RLS** — full read/write on every table in the project |

The value is not reproduced in this document, in any commit, or in any test. It was
never used against the live API during investigation, and it was not submitted to any
third-party scanning service.

## 1. Exposure timeline

Reconstructed from git history and the GitHub API. Every date is verifiable.

| Date | Event |
|---|---|
| 2026-02-11 | Key issued by Supabase |
| 2026-02-13 | `d608bb3d` — key present in `backend/.env.example` |
| 2026-04-11 | `c3adba34` "chore(setup): sanitize env templates…" — removed from HEAD |
| **2026-04-12** | **GitHub repo created as PUBLIC**, with full history imported — including the key-bearing commits |
| **2026-04-15** | `33f252b4` — **key re-added**, present again in HEAD of a public repo |
| 2026-04-20 | `06659006` — removed from HEAD again |
| 2026-07-30 | Discovered. Not in `origin/main` HEAD; **still in public history**. Rotation begun. |

Two things this timeline establishes that a casual reading would miss:

1. **The 2026-04-11 sanitization did not hold.** The key was reintroduced four days
   later and sat in the browsable tree of a public repo for roughly five days.
2. **Removing it from HEAD never reduced the exposure.** The commits remained
   world-readable, and the key was never rotated, so it stayed live regardless.

Repository state at time of writing: `private: false`, `visibility: public`,
`forks_count: 0`, `allow_forking: true`, `stargazers_count: 1`, `watchers_count: 1`.
Zero forks is mildly reassuring — no fork network propagated the history — but **GitHub
does not track clones by actor**, and automated scanners crawl public repositories
continuously. A signed Supabase `service_role` JWT in a public repo should be treated
as harvested, not as possibly-missed.

## 2. Data categories reachable with this key (runbook §1a)

`service_role` bypasses RLS, so the answer is *everything in the project*. Enumerating
PII-bearing tables from `backend/migrations/` gives 14:

`admin_staff`, `staff`, `cloud_messages`, `corporate_email_otp_records`,
`driver_location_history`, `email_send_log`, `email_suppressions`,
`emergency_contacts`, `fare_split_participants`, `marketing_consent_events`,
`marketing_suppressions`, `ride_routes`, `safety_incidents`, `venues`.

**That list is incomplete and understates the exposure.** `users`, `drivers`, and
`rides` — the three largest PII holders — predate the migrations directory (created via
the Supabase dashboard or an earlier bootstrap), so they do not appear in a scan of
`CREATE TABLE` statements. From CLAUDE.md and the code, the key also reaches:

| Category | Where | Runbook §1c trigger |
|---|---|---|
| Full names, phone numbers, email addresses | `users` | identity theft / impersonation |
| Driver full home address (encrypted at rest) | `drivers` | **physical harm — stalking** |
| Driver licence details, background-check status | `drivers` | **government ID** |
| Vehicle VIN and plate | `drivers` | identity / vehicle tracing |
| Precise pickup/dropoff coordinates and GPS traces | `rides`, `ride_routes`, `driver_location_history` | **precise location** |
| Emergency contact names and phones | `emergency_contacts` | third parties who never used the app |
| Safety incident reports with coordinates | `safety_incidents` | highly sensitive |
| Stripe customer/payment-method references | `users`, `rides` | financial account info |
| Corporate account, membership and billing data | `corporate_*` | business confidential |

Raw card numbers are **not** exposed — Stripe holds those and the codebase never stores
a PAN. Stripe *customer* and *payment-method* identifiers are exposed, which permit
correlation but not direct charging without the Stripe secret key. Note however that
Stripe and Twilio credentials live in the `app_settings` **table** (CLAUDE.md,
"Settings in DB") — so this key also reads **those secrets**, which is a credential-
chaining path to the payment and SMS providers. That is the most serious secondary
exposure and it is why rotation cannot stop at Supabase.

## 3. PIPEDA "real risk of significant harm" (runbook §1c)

Working the runbook's checklist against the facts above:

| Question | Answer |
|---|---|
| Does the exposed data include government ID, SIN, or financial account info? | **YES** — driver licence details, Stripe references |
| Does the exposed data include precise location (GPS)? | **YES** — pickup/dropoff coordinates and driver location history |
| Does the exposed data enable identity theft or impersonation? | **YES** — name + phone + email + address + licence |
| Is the breach confined to internal staff with no external access? | **NO** — the repository was public |
| Could the breach enable physical harm (stalking, home address)? | **YES** — driver home addresses and rider trip endpoints |

**Five of five triggers are met.** The runbook's rule is that *any* box checked means
proceed to notification. On these facts the threshold is not a close call.

**Assessment: the "real risk of significant harm" threshold appears to be met, and
Privacy Commissioner notification within 72 hours is very likely mandatory.** This is
an engineering assessment of the technical facts, not a legal determination — the
notification decision belongs to whoever owns privacy/legal for Spinr, and they should
make it with this document in hand. What engineering can say without qualification is
that the exposure was public, the credential was maximally privileged, and it was live
for the entire window.

## 4. What has been done

- [x] **Key rotation** — owner-actioned, confirmed in progress 2026-07-30. This is the
      only step that actually closes the exposure.
- [x] **Detection fixed** — `.gitleaks.toml` had no `[extend]` stanza, so passing
      `--config` replaced gitleaks' ruleset with zero rules and both secret-scanning
      CI jobs reported "no leaks found" for any input. That is why this sat undetected
      for 3.5 months with a green dashboard. Fixed in `a29cb801`; full history now
      returns 29 triaged findings instead of a vacuous zero.
- [x] **Exposure window established** — this document.
- [x] **Verified the key is absent from current `origin/main` HEAD.**

## 5. What still must be done — and needs access I do not have

Each item below is blocked on credentials or org administration that this session
cannot reach. They are the difference between "we know a key leaked" and "we know what
happened."

1. **Rotate the downstream secrets in `app_settings`.** The Supabase key reads that
   table, so the **Stripe secret key, Twilio credentials, and Google Maps API keys**
   must be treated as exposed too and rotated. Rotating only the Supabase key leaves
   the chained credentials live. *This is the highest-priority remaining action.*
2. **Confirm the leaked project `ref` is current production.** The repository contains
   no concrete Supabase project ref — every template is a placeholder — so the leaked
   ref could not be compared against production from here. If it points at a dead or
   staging project the blast radius collapses; if it is production, everything above
   stands. **Nothing else should be concluded until this is answered.**
3. **Pull Supabase audit / Postgres logs for 2026-04-12 → rotation.** This is the only
   way to know whether the key was *used* by anyone. Look for service-role requests
   from unfamiliar IPs, bulk `SELECT`s against `users`/`drivers`/`rides`, and any
   access to `app_settings`. Absence of evidence here is what would downgrade this from
   "assume harvested" toward "exposed but unused."
4. **Count affected individuals** (runbook §1b) — requires row counts for `users`,
   `drivers`, `rides`, `emergency_contacts`. Not derivable from the repository.
5. **GitHub audit**: repository clone/traffic data for the window, and whether GitHub's
   own secret scanning raised an alert that went unactioned.
6. **Decide and execute notification** (runbook §4) — Privacy Commissioner within 72h
   of the risk determination, and affected-individual notification. Owner/legal call.
7. **Decide on history rewriting.** Rotation makes the leaked key worthless, which is
   the real remedy. Rewriting history with `git-filter-repo`/BFG does **not** undo
   public exposure — anything already cloned or harvested is gone — and it breaks every
   existing clone. Recommendation: **rotate, do not rewrite**, unless legal advises
   otherwise for the record.

## 6. Why this went undetected for 3.5 months

Worth recording, because the detection failure is more systemic than the leak:

- The repository had **two** secret-scanning CI jobs. Both loaded **zero detection
  rules** because `.gitleaks.toml` omitted `[extend] useDefault = true`, and githeaks
  *replaces* rather than merges its ruleset when given `--config`. Neither job could
  report a finding for any input, and both reported green.
- The `.gitleaks.toml` allowlist additionally excluded `\.next/`, which is exactly the
  directory the bundle-scan job exists to scan — a second, independent reason that job
  was inert.
- The pre-commit hook's secret check is a 10-pattern grep over staged changes only. It
  does not match a Supabase `service_role` JWT, and it never sees history.

So the leak was not missed by a scanner that was looking. Nothing was looking. The
lesson recorded in the T5 change log applies here: **a clean result from a tool that
cannot fail is not evidence** — verifying a scanner requires planting a canary, not
reading its output.

## 7. Preventing recurrence

| Control | Status |
|---|---|
| gitleaks actually loads rules | **Done** (`a29cb801`) |
| Bundle scan can see `.next/` | **Done** (`a29cb801`) |
| History scan baseline triaged to 29 findings | **Done** — documented per-finding |
| Make the history scan merge-blocking | **Blocked** on rotating this key; the baseline cannot be honestly called clean until then |
| Pre-commit hook detects Supabase JWTs (`eyJ…` with `role: service_role`) | **Not done** — a narrow, high-value addition; the current 10 patterns miss it |
| `.env.example` files contain only placeholders, enforced in CI | **Not done** — this file was sanitized once and regressed 4 days later, so a one-off cleanup demonstrably does not hold |

The last two are the controls that would have caught this specific failure twice over,
and neither exists yet.
