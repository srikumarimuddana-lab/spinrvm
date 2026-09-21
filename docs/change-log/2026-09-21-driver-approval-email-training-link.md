# Change Impact & Risk Log — Driver approval email names the training link

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-21 |
| Author | Claude Code (session), for mkkreddy52@gmail.com |
| Surface(s) | backend (driver-facing email copy only) |
| Domain (Sentry tag) | drivers |
| PR / commit link | branch `claude/driver-training-link-missing-4ui9sw` |
| Related issue or gap ID | Live-testing report: approved driver saw no training link in the "You're Approved! 🎉" email |

## 1. Issue / gap identified

A driver approved during live testing received the "You're Approved! 🎉" email and it never
mentioned driver training — its only next step was *"Open the Spinr driver app, tap Go Online,
and you'll start receiving ride offers."* Reported with a screenshot of the received email.

## 2. Root cause

The training host (`training.spinr.ca`) existed in exactly **one** driver-facing place: the
welcome email sent at registration (`backend/utils/driver_emails.py`), where it reads *"Complete
your driver training at training.spinr.ca before your first ride."*

Nothing repeated it later. The approval email is built from a separate module
(`backend/utils/driver_status_notifications.py`), whose `_EMAIL_NEXT_STEPS["active"]` entry only
ever pointed at Go Online. So the sequence a real driver experiences was:

1. **Signup** — told to train "before your first ride", one line among seven, with no ride in sight.
2. *(days or weeks pass — document review)*
3. **Approval** — told to go online and start taking offers, training not mentioned.

This is not only an omission but a **contradiction**: the approval email actively invited the
driver to do the exact thing the welcome email said required training first. Confirmed by grep
that this was the whole picture — the driver app has zero training references, and the only
training endpoint in the backend is `GET /api/admin/drivers/{driver_id}/training`, which is
admin-only (admins can *view* LMS progress; nothing ever routes the driver to it).

## 3. Fix / remediation

Name the training host in the approval email's next-step copy, ahead of the Go Online
instruction, and render it as a real link — reusing the exact `links={}` mechanism the welcome
email already uses.

To avoid a second copy of the URL drifting from the first, `_TRAINING_HOST` in `driver_emails.py`
was promoted to a public `TRAINING_HOST` and is now the single source for both emails. The
approval copy carries a `{training}` placeholder resolved at send time, matching the module's
existing `{support}` / `{app_name}` idiom, which keeps the copy maps pure and import-free.

**Deliberately NOT changed** (out of the scope the requester confirmed):
- The driver app — still has no training entry point anywhere.
- `go_online` — still has **no** training gate; nothing enforces completion.
- The "Account Verified! ✅" email (`_VERIFICATION_NEXT_STEPS`) — a different notice from the
  approval email, left as-is.

## 4. Risk & impact on existing functionality

**Blast radius: isolated — backend email copy only.** No schema, no state machine, no money path,
no background loop, no API contract.

Blast-radius grep performed on every symbol touched (`_EMAIL_NEXT_STEPS`, `_email_fields`,
`_email_payload`, `EMAIL_STATUSES`, `_send_status_email`, `status_message`, `action_message`,
`verification_message`) plus every usage of `_TRAINING_HOST`. Consumers found:

| Consumer | Reaches changed copy? | Effect |
|---|---|---|
| `routes/admin/drivers.py:2228` — `action_message("approve")` | **Yes** | Approval email gains training line |
| `routes/admin/drivers.py:2303` — `status_message(status)` override | **Yes**, when status is `active` | Same |
| `routes/admin/documents.py:474` — `status_message("active")` on doc approval | **Yes** | Same — this is the path that produced the reported email |
| `routes/admin/drivers.py:1997` — `verification_message(...)` | No | Uses `_VERIFICATION_NEXT_STEPS`, untouched |
| `routes/drivers/profile.py:319`, `documents.py:472` — `status_message("needs_review")` | No | `needs_review` is not in `EMAIL_STATUSES` (push-only) |
| `utils/driver_emails.py` — welcome email | Constant renamed only | Same rendered output |

The three **blocking** emails (rejected / suspended / banned) are explicitly unaffected: the
`links` map is passed only when the host actually appears in the paragraphs, so those render
byte-identically to before — which is what the existing snapshot tests depend on. A regression
test pins this.

`_linkify` is a literal, post-escape replace over call-site constants, so adding `links` cannot
inject markup or interact with the admin-authored suspension/rejection reason text.

Known, accepted edge case: `{training}` is substituted across **all** statuses' paragraphs, which
includes the admin-authored `reason` text appended to suspended/rejected bodies. An admin who typed
the literal string `{training}` into a suspension reason would see it become `training.spinr.ca` in
that email. This is pre-existing behaviour extended by one token — `{support}` and `{app_name}`
have always been replaced over the same text — the input is admin-authored rather than
user-controlled, and there is no security or privacy consequence, so it is left consistent with the
established pattern rather than special-cased.

**Risk of the shared constant:** `driver_status_notifications.py` was deliberately import-light
(stdlib only at module level). `TRAINING_HOST` is imported **lazily inside `_send_status_email`**,
alongside the lazy imports already there, so that property is preserved. Verified no import cycle:
nothing in the `driver_emails` → `email_layout` / `email_notifications` / `company_details` chain
imports `driver_status_notifications`.

## 5. User-experience effect

**Driver-facing, and intentionally visible.** An approved driver now reads:

> Complete your driver training at **training.spinr.ca** before your first ride. Open the Spinr
> driver app, tap Go Online, and you'll start receiving ride offers.

- Not visible mid-session: this is a one-time lifecycle email, not an in-app surface. No driver
  currently online or on a trip sees any change.
- Riders, corporate admins, internal admins: no change.
- Wording deliberately reuses the welcome email's "before your first ride" so the two messages
  agree rather than contradict.
- The subject, heading, and push notification are unchanged. Within the email's second paragraph,
  the existing Go Online sentence is **byte-identical** to what already shipped — training was added
  as a new sentence in front of it, not a rewrite of it.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/driver_emails.py` | `_TRAINING_HOST` → public `TRAINING_HOST`; comment records shared ownership | One source of truth for the address across both driver emails |
| `backend/utils/driver_status_notifications.py` | `_EMAIL_NEXT_STEPS["active"]` names training via `{training}`; `_send_status_email` resolves it and passes `links` | The actual fix |
| `backend/scripts/preview_notification_templates.py` | Approval preview copy + `links` map updated to mirror the real email | Preview hardcodes copy by design; left stale it would misrepresent what ships |
| `backend/tests/test_driver_status_email_training_link.py` | New regression test | Pins both approval paths, and pins that blocking emails gain nothing |
| `backend/scripts/preview_notification_templates.py` | Three justified `# noqa` (S310 ×2, S603) on **pre-existing** lines | Unrelated to this fix — see note below |

### Note: an unrelated gate this change had to unblock

The pre-commit hook lints whole staged files, so staging the preview script surfaced three ruff
findings that **already existed on `main`** (`S310` ×2 on a `urllib` fetch, `S603` on the Chrome
`subprocess.run`) and blocked the commit. Confirmed pre-existing by re-running `ruff check` against
a clean stash of HEAD — none come from this diff.

Rather than bypass with `--no-verify` (the hook labels that emergency-only), each was given a
justified `# noqa` in the style the file already uses for `BLE001`: the fetch URL is built from the
operator's own `SUPABASE_URL` rather than user input, and `chrome` is a path the script's own
`_find_chrome()` verified with `is_file()` against a fixed candidate list, with all other args
literals. This is a dev/QA tool whose own docstring states it is "not part of the production
runtime". The script was re-run afterwards and still renders correctly.

Per CLAUDE.md pre-merge gate 8, a check red for reasons unrelated to the diff is gate decay, not
"not my problem" — the accepted risk is now documented inline instead of silently blocking the next
person who touches this file. No behaviour changed.

## 7. Before / after

```python
# Before — backend/utils/driver_status_notifications.py
_EMAIL_NEXT_STEPS: dict[str, str] = {
    "active": "Open the {app_name} driver app, tap Go Online, and you'll start receiving ride offers.",
```

```python
# After
_EMAIL_NEXT_STEPS: dict[str, str] = {
    "active": (
        "Complete your driver training at {training} before your first ride. "
        "Open the {app_name} driver app, tap Go Online, and you'll start receiving ride offers."
    ),
```

Rendered (approval email, second paragraph):

```
# Before
Open the Spinr driver app, tap Go Online, and you'll start receiving ride offers.

# After
Complete your driver training at training.spinr.ca before your first ride. Open the Spinr
driver app, tap Go Online, and you'll start receiving ride offers.
```

## 8. Rollback plan

`git revert` **is** a sufficient rollback here, and this is one of the cases the policy allows it:
the change writes nothing to the database, touches no live data (no Stripe charge, wallet delta,
ride state, or insurance-period row), and alters no schema or API contract. The only artifact is
the text of emails already delivered — which a revert cannot and need not recall.

No feature flag was added. Justification: the change is a one-paragraph copy edit on a
transactional email, it is not a new UX surface, and this notification module has no existing
flag mechanism. Adding a DB-backed flag for a single sentence would be more machinery than the
change itself and would not reduce the (already negligible) blast radius.

## 9. Verification performed

- [x] **Blast-radius grep performed** — every symbol listed in §4, plus all `_TRAINING_HOST` usages
      and a repo-wide sweep for training/LMS references across backend, driver-app, and admin-dashboard.
- [x] **Logic verified end to end by direct execution** — `driver_status_notifications` is
      stdlib-only at module level, so it was imported and exercised directly. Confirmed on **both**
      approval paths (`action_message("approve")` and `status_message("active")`, the latter being
      the path that produced the reported email): the host appears, no `{...}` placeholder leaks,
      and the `links` map is populated. Confirmed for `reject` / `suspend` / `ban`: host absent and
      `links is None`.
- [x] **HTML anchor verified against the real `_linkify`/`_esc`** — renders
      `<a href="https://training.spinr.ca" ...>training.spinr.ca</a>`; and a paragraph with an
      empty links map renders byte-identically to plain escaping.
- [x] **Caught and fixed a real regression during self-review.** An earlier draft of this change
      joined the two sentences with a dash (`"... before your first ride — then open the {app_name}
      driver app"`), which lowercased `Open` and would have broken
      `tests/test_driver_status_email_app_name.py:42` (`assert "Open the Spinr driver app" in body`)
      — a CI failure, caught here only because the old copy was grepped for in the test suite rather
      than assumed unused. The copy was restructured so the shipped sentence survives byte-for-byte,
      and a new test (`test_training_line_is_additive_and_leaves_the_shipped_sentence_intact`) now
      fails first and names the reason if anyone reflows it again. All three assertions in that
      sibling test were re-checked by direct execution and pass.
- [x] **Rendered end to end via the repo's own preview tool** —
      `python scripts/preview_notification_templates.py` runs standalone (stdlib only) and produces
      the approval email with `training.spinr.ca` as a live anchor; exactly 2 anchors appear across
      the whole driver-email preview (welcome + approval), confirming no other email picked one up.
- [x] **Driver-classification check (explicit, per `regulatory-sk.md`)** — "Complete your driver
      training at ... before your first ride" was checked against CLAUDE.md's and
      `regulatory-sk.md:90-95`'s Forbidden-patterns list (mandatory shifts / minimum hours,
      required uniforms, "employee handbook"/"manager"/"performance review" wording, penalties for
      going offline, employer-style benefits) and matches **none** of them: no "must", "required",
      or consequence language, and no code enforces it (`go_online` in `routes/drivers/status.py`
      has no training check — grepped for `training_completed` / `training_required` backend-wide,
      neither exists). It reads as platform-safety onboarding sequencing, not control-of-work.
      Training is also **not** one of `regulatory-sk.md`'s SK Transportation Act / SGI eligibility
      items — per `docs/runbooks/saskatoon-launch.md` J-4 it is Spinr's own onboarding walkthrough —
      and the copy correctly does not claim otherwise. Logging this here because the identical
      sentence has shipped in the welcome email since 2026-08-28 without that specific check ever
      being recorded (`docs/change-log/2026-08-28-driver-welcome-email-training-subscription-copy.md`
      documents only the fee/commission claim), so this entry closes that procedural gap rather
      than inheriting it.
- [x] **Lint + format clean** — `ruff check` and `ruff format --check` pass on all four files.
- [x] **Syntax/compile check** — all four files compile.
- [x] **Reviewed against CLAUDE.md conventions** — dual-import pattern preserved on the new import;
      module's import-light property preserved via lazy import; no error swallowed (the existing
      best-effort email guard is unchanged and still logs); no PII added to the payload (the email
      body gains a constant URL, no driver data).
- [ ] **pytest suite NOT run** — see below.
- [ ] Manual repro in staging — not performed.
- [ ] Feature flag — not added (justified in §8).

## 10. What was NOT verified

Stated explicitly rather than left to silence:

- **The pytest suite did not run in this environment.** The backend has no dependencies installed
  (`httpx`, `fastapi`, `pydantic`, `anyio`, `pytest` all missing) and this session's network policy
  blocks PyPI — `pip install` fails with `403 Forbidden` on CONNECT through the agent proxy, and
  PyPI is in the proxy's `noProxy` list so it has no direct route either. The new regression test
  `test_driver_status_email_training_link.py` is therefore **written but never executed**; it needs
  a CI run or a developer machine to confirm it passes. The verification above substitutes direct
  execution of the pure logic, which covers the copy resolution and the link rendering but **not**
  the `send_lifecycle_email` fan-out, the recipient lookup, or the anyio/pytest fixtures the test
  relies on.
- **No email was sent or visually inspected.** The rendered HTML was verified as a string, not
  opened in a mail client. No check of how the link renders in Gmail/Outlook specifically.
- **No staging run.** The end-to-end path (admin approves → email actually arrives with a working
  link) has not been exercised against a real environment.
- **The underlying product gap is untouched and remains open**: the driver app still has no
  training entry point, `go_online` still does not check training completion, and the LMS
  integration is still admin-read-only. This change makes the driver *aware* of training at the
  right moment; it does not make training discoverable in-app or enforceable.
- **Whether `training.spinr.ca` is live and serves a working driver-facing page was not verified** —
  the constant matches the default `lms_api_base_url` the admin integration points at, but no
  request was made to it from this session.
