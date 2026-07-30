# Change Impact & Risk — the secret-scanning gate detected nothing (T5)

**Date:** 2026-07-30 · **Branch:** `claude/critical-security-pipeda-breach-pn67ww`
**Surface:** CI / security gates · **Risk:** high — this masked a live credential exposure
**Related:** `docs/LAUNCH_GATE_IMPLEMENTATION_PLAN.md`, `docs/runbooks/data-breach.md`

Launch-gate item: *"Make secrets, SAST, dependency, and code-security scans
merge-blocking."* Hard no-go: *"Security findings can pass CI without blocking merge."*

> ## ⚠️ P0 — a live Supabase `service_role` key is in git history
>
> Found while verifying this gate. **Rotation is owner-actioned and in progress.**
> Details in "The P0 this was hiding" below. The key is not in `HEAD`; it is in
> history and was never rotated, so it is still valid.

---

## Issue / gap identified

Three separate defects, each of which independently made a security gate report
green while detecting nothing:

1. **`.gitleaks.toml` loaded zero detection rules.** Passing `--config` makes
   gitleaks **replace** its built-in ruleset, not merge with it. The file defined an
   `[allowlist]` and no `[[rules]]` and no `[extend]`, so both the G5a history scan
   and the G5b bundle scan ran with **no detectors at all** and reported
   "no leaks found" for any input.
2. **`.gitleaks.toml` excluded `\.next/`, which is exactly what G5b scans.** The
   G5b job scans `admin-dashboard/.next/static` for `NEXT_PUBLIC_*` secrets baked
   into the shipped bundle. With that path allowlisted, the job could not report a
   finding by construction — a second, independent reason G5b was dead.
3. **`.semgrep/spinr-rules.yml` had never parsed** (fixed in `b584d69`), and
   `|| true` swallowed the resulting exit 7.

## Root cause

The same pattern in all three: **a swallow (`|| true` / `continue-on-error: true`)
sitting in front of a tool that was failing to run.** A gate that finds nothing and
a gate that *cannot* find anything are indistinguishable once you discard the exit
code, and both look like success on the dashboard.

The gitleaks config compounded it. The file's own header says its purpose is to
"suppress false positives **without disabling the scanner**" — which is precisely
what it did not do. Nothing in the file signals that omitting `[extend]` disables
every rule, and the failure mode is silent: no warning, no error, exit 0.

## How it was caught

Not by reading the config — by **canary-testing it**. A scan of the real 8.4 MB
admin bundle completed in 13.7 ms, the same as a scan of an empty directory, which
is what prompted planting a file containing an obvious AWS key and an `sk_live_`
Stripe key:

| Scan | Result |
|---|---|
| canary, **with** `.gitleaks.toml` | `no leaks found` |
| canary, **without** `--config` | `leaks found: 2` |
| canary, with `[extend] useDefault = true` added | `leaks found: 2` |
| canary placed under `.next/static`, rules restored | `no leaks found` (path allowlist) |

That is the whole diagnosis: the config, not gitleaks, was the problem — twice.

## The P0 this was hiding

With rules actually loaded, the full-history scan surfaced a **real
`SUPABASE_SERVICE_ROLE_KEY`** committed to `backend/.env.example`:

| Property | Value |
|---|---|
| Claims | `iss: supabase`, `role: service_role`, real 20-char project `ref` |
| Signature | HS256, 43 chars — a genuine signed token, not a placeholder |
| Issued | 2026-02-11 |
| Expires | 2036-02-12 — **10-year validity, still valid today** |
| In `HEAD`? | **No** — sanitized by `c3adba34` ("chore(setup): sanitize env templates") |
| In history? | **Yes** — present across ~5 commits (2026-04-01 … 2026-04-27 range) |

Why this is a P0 and not a stale-template nit:

- `service_role` **bypasses all RLS**. It is the most privileged credential in the
  Supabase project — full read/write over every rider, driver, ride, and payment row.
- Removing it from `HEAD` did nothing to invalidate it. `c3adba34` sanitized the
  template but **no rotation followed**, so the credential in history still works.
- Anyone with repository read access at any point since February has had it.

Deliberately **not** done here: the key was never tested against the live API
(that would be using the credential), and neither the key nor the project ref
appears in this document, the commit messages, or any test fixture.

**Action:** rotation in the Supabase dashboard, owner-actioned, confirmed in
progress at the time of writing. Rotation is the step that closes this — history
rewriting does not, because it cannot reach clones that already exist.

## Triage of the full baseline

Raw history scan: **44,492** findings. That number is why this could not simply be
flipped to blocking.

| Stage | Count | What changed |
|---|---:|---|
| Rules loaded, no exclusions | 44,492 | baseline |
| `frontend/.metro-cache/` excluded | **29** | 44,487 were `generic-api-key` entropy hits on a committed Metro build cache |

The surviving 29, triaged individually:

| Rule | n | Verdict |
|---|---:|---|
| `generic-api-key` | 24 | **False positives.** `google-services.json` (Firebase client config — designed to be embedded in the app and public), `eas.json` (Expo build config), and test fixtures. |
| `jwt` | 2 | **REAL** — the `service_role` key above, at two commits. |
| `jwt` | 1 | False positive — a 128-char test token in `backend/tests/conftest.py`. |
| `private-key` | 1 | False positive — `backend/.env.example:65`, a placeholder (`"project_id":"your-project"`, `"private_key":"-----BEGIN PRIVATE KEY-----\n...\n"`). |
| `private-key` | 1 | False positive — `docs/runbooks/live-ride-activity-go-live.md:86`, prose *describing* the PEM format an operator must paste. |

**One real finding out of 44,492.** Which is the argument for fixing the config
rather than for leaving the gate off: the signal was always there, buried under
noise that a five-line exclusion removes.

## Semgrep rule tuning (same commit family)

`b584d69` made the Spinr ruleset parse; its first real run produced 74 findings.
Triage showed these were dominated by **rule defects, not code defects**, so the
rules were tightened rather than the code bent to fit them:

| Rule | Before | After | Why the findings were wrong |
|---|---:|---:|---|
| `spinr-jwt-role-must-reread` | 62 | 0 | `$TOKEN` was unconstrained, so it matched **any** `.get("role")` — including `user.get("role")` on a users-table row, which is the pattern CLAUDE.md *prescribes*. 31 hits were on `admin`, 18 on DB rows, 10 on unrelated objects, 3 on `payload` inside admin-audience checks (admin JWTs are fully trusted by design). Now constrained to token-shaped receivers, with admin scopes excluded. |
| `spinr-no-float-in-money` | 6 | 3 | Flagged `fare_service._f()` (`return float(v)`) and `_fd()` (`return float(_round(_d(v)))`) — the exact Decimal→float helpers CLAUDE.md **mandates** at the response boundary, plus `float(Decimal(...).quantize(...))`. The rule cited the convention while flagging it. Now excludes the sanctioned helpers and already-rounded values. |

74 → 12 → **9** remaining, with honest verdicts:

- `fare_service.py:294` — `float(surge_multiplier)` inside an f-string display label. Not money arithmetic.
- `fare_service.py:302` — `if float(val) > 0:`. A comparison.
- `fare_service.py:303` — `"amount": float(val)` on a receipt line item. **Arguably real**: it bypasses `_f()`/`_round()`, so a fee amount reaches the API response unrounded. Deliberately *not* drive-by fixed — it changes a displayed money value and belongs in its own change with its own tests, not buried in a CI commit.
- `driver_repo.py:323` — `spinr-ride-state-needs-guard` on `claim_driver_atomic`. **False positive**: that function *is* the state-machine guard, the documented atomic conditional UPDATE CLAUDE.md describes for the acceptance race.
- `payments.py:224` — `spinr-stripe-idempotency-static` on a time-bucketed key (`intent-{user}-{minute}`). Not static; the bucketing is intentional double-tap dedupe.
- `scripts/manage_admin.py` ×4 — `print(f"...phone: {phone}")` in a local operator CLI. Low risk (not a log sink, prints what the operator typed), but genuinely could use `redact_phone()`.

## Files modified

| File | What changed | Why |
|---|---|---|
| `.gitleaks.toml` | Added `[extend] useDefault = true`; removed build-artefact `paths` (incl. the `\.next/` entry that killed G5b); added `\.metro-cache/` | Restore detection; unblock G5b; make the baseline readable |
| `.semgrep/spinr-rules.yml` | Constrained `spinr-jwt-role-must-reread` to token receivers + admin exclusions; excluded the sanctioned money helpers from `spinr-no-float-in-money` | Stop flagging the patterns CLAUDE.md prescribes |
| `.github/workflows/security-gates.yml` | G5a reverted to `continue-on-error: true` with the reason recorded | It was flipped on the strength of a vacuous scan; see below |
| `docs/change-log/2026-07-30-secret-scanning-gate-was-vacuous.md` | New — this file | Required by CLAUDE.md |

## A correction to my own earlier change

Earlier in this branch I set G5a `continue-on-error: false`, citing "verified clean:
5487 commits, no leaks." **That verification was worthless** — it used the
rule-less config. Re-running with rules loaded returned 44,492 findings. The flip
has been reverted to `true`, with a comment recording why, so nobody "cleans up"
the apparent inconsistency later.

This is the exact failure the gate itself had: *a clean result from a tool that
cannot fail is not evidence.* Verifying a gate now means canary-testing it, not
reading its output.

## Rollback plan

`git revert` is safe for all three files — they are CI configuration and rule
definitions. No migration, no data, no runtime code.

Reverting `.gitleaks.toml` **restores a scanner that detects nothing**, so it is
never the right response to noise. If the 29-finding baseline is too noisy, add a
justified `paths`/`regexes` entry (with evidence, as the new comments model) rather
than removing `[extend]`.

Note the one-way door: **rotation of the leaked key cannot be rolled back**, nor
should it be. Any deploy still holding the old `SUPABASE_SERVICE_ROLE_KEY` in its
environment will start failing DB calls until the new value is set — that is
expected, and it is a deploy-config update, not a rollback trigger.

## Verification performed

- **Canary-tested in both directions** — the only verification that means anything
  for a scanner. Table above. The fixed config finds a planted AWS key and Stripe
  `sk_live_` key; the old config finds neither.
- **G5b now genuinely scans.** Real bundle scan went from 13.7 ms / 0 findings
  (vacuous) to 703 ms / 2 findings over 8.4 MB of `.next/static`. Both findings
  triaged as false positives: 20-char all-uppercase-letter runs in a minified
  chunk, entropy 2.12 and 2.46, not starting with `AKIA`. Real AWS key IDs mix
  letters and digits at ~3.5+ entropy (the canary measured 3.68). Confirmed by
  character-class analysis without echoing the values.
- **Admin build verified green** (`npm run build`, exit 0) so the bundle scanned was
  a real production build. The first attempt failed on `motion` being absent from
  `node_modules`; `motion@12.43.0` is in `package-lock.json`, so that was an
  incomplete sandbox install, **not** a repo defect — `npm ci` fixed it. Called out
  because "the admin build is broken" would have been a wrong and alarming claim.
- **Semgrep**: YAML parses (10 rules); findings 74 → 12 → 9, each remaining one
  triaged above with a real/false verdict rather than a count.
- **bandit** 0 findings at the gate's high/high thresholds; **pip-audit** clean via
  the PyPI advisory service.
- **eslint**: admin-dashboard 0 errors / 319 warnings (under its 600 budget) — that
  gate is enforceable and was flipped. rider-app and driver-app **cannot currently
  run eslint at all** (rider-app pins eslint `^8.57.0` but its Expo flat-config
  runner needs eslint 9's `eslint/config` export; driver-app crashes in
  `@eslint/config-array`/minimatch), so their finding counts are unknown and their
  step now fails loudly on exit ≥ 2 instead of passing silently.

## What was NOT verified

- **Whether the leaked key targets the current production project.** The repo
  contains no concrete Supabase project ref — every template is a placeholder — so
  the leaked `ref` could not be compared against production from here. Only the
  owner can confirm that, and it changes the blast radius substantially.
- **Whether the key was ever actually used by a third party.** That needs Supabase
  audit/access logs for the exposure window; not derivable from the repo. Required
  for the `docs/runbooks/data-breach.md` "real risk of significant harm"
  determination and the PIPEDA 72-hour notification decision.
- **Who had repository read access between 2026-02-11 and rotation.** Needed to
  scope the exposure; a GitHub org-audit question, not a code question.
- **The 24 `generic-api-key` findings were triaged by file and rule, not by
  inspecting every matched value.** `google-services.json` and `eas.json` contain
  intentionally-client-embedded Firebase/Expo identifiers, which is the basis for
  calling them false positives — that is a category judgement, not 24 individual
  confirmations.
- **CI behaviour is inferred, not observed.** All scanners were run locally. The
  workflow's own YAML parses and the embedded validator was tested both ways, but
  no CI run has exercised the edited jobs. `pip-audit` in CI uses OSV (unreachable
  from this sandbox) rather than PyPI, so its first enforced run could differ.
- **`frontend/.metro-cache/` is still committed.** It is now excluded from scanning,
  which fixes the noise but not the underlying problem of a build cache in git.
  Removing it is a separate change.
