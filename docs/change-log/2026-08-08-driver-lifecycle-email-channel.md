# Change Impact & Risk Log — Driver lifecycle + document-expiry email channel

## Summary

| Field | Value |
|---|---|
| Date | 2026-08-08 |
| Author | Claude Code (session-driven) |
| Surface(s) | backend |
| Domain (Sentry tag) | drivers, admin |
| PR / commit link | `1c32fde`, `8461ec1`, `1ee324c`, `95a168c`, `a328790`, `1397093`, `ad45909` |
| Related issue or gap ID | Full audit in `docs/notification-channel-coverage.md` (gaps D5, D7/D8, D10–D13, D15–D18, X1) |

## 1. Issue / gap identified

An audit of every rider- and driver-facing event found the email channel almost
entirely unused for lifecycle events. Push carried nearly everything (~97 call
sites); email existed for 14 flows, only one of which was a rider lifecycle
event (the ride receipt).

Concretely, on the driver surface:

1. **D5** — approving a driver's last pending document can flip them from
   `needs_review` back to `active`. That sent nothing. The rejection path a few
   lines below notified, so the asymmetry was: told when it goes wrong, silent
   when it goes right.
2. **D10–D12** — document-expiry warnings (7-day / 1-day / today) were push-only.
3. **D13** — the expired-document auto-suspension push fired at **default**
   priority, so a driver who had turned push notifications off got no notice at
   all that they had been taken offline and could no longer earn.
4. **D15–D18** — approval, rejection, suspension and ban were push-only.
5. **D7/D8** — `admin_verify_driver` sent its own push directly rather than
   through the policy module, so it lacked the `deleted_at` recipient guard (the
   one documented exception in `docs/driver-lifecycle-status-flow.md`).
6. **X1** — `notification_preferences.email_enabled` was a dead column:
   persisted, surfaced in `rider-app/app/settings.tsx:97` as an "Email
   Notifications" toggle, and read by no send path.
7. **Branding** — no Spinr email contained the Spinr logo. The asset exists at
   `backend/static/branding/spinr_logo.png` but was referenced only by report
   PDF headers; the two emails with any shell rendered the wordmark as
   `<h1>Spinr</h1>` text.

## 2. Root cause

Push and email grew as two unrelated stacks. `features.send_push_notification`
is a single well-used entry point that also writes the in-app inbox row, so
"notify the user" naturally meant "push". Email had no equivalent layer — every
sender hand-built its own HTML and called `send_transactional_email` directly —
so adding email to an event meant writing a template, and nobody did.

D5 and D7/D8 are the residue of the 2026-07-30 lifecycle-notification work,
which consolidated three call sites into one policy module but left
`admin_verify_driver` and the document-approval path outside it (both recorded
as known gaps at the time).

D13 is a missed tier: the `account` priority added in migration 272 was applied
to the admin-initiated suspensions but not to the background loop's, even though
both leave the driver unable to earn.

## 3. Fix / remediation

**New, additive infrastructure (nothing reads it until step 2):**

- `backend/routes/branding.py` — `GET /api/v1/branding/spinr-logo.png`. Email
  clients cannot read a file off disk, and `_build_mime` has no
  `multipart/related` support, so the logo needs a URL. **Deliberately a
  single-file route, not `app.mount("/static")`** — `backend/static/` also holds
  `sgi_forms/D00032_*.pdf` and `D00033_*.pdf`, the SGI regulator templates,
  which a directory mount would publish.
- `backend/utils/email_layout.py` — `render_email()` returning both HTML and a
  plain-text alternative, so `send_transactional_email` builds a real
  `multipart/alternative`. Logo header, brand tokens defined once, footer
  reusing `report_branding.COMPANY_LINE` / `COMPANY_CONTACT_LINE`, and
  escaping of every caller-supplied value.
- `backend/utils/email_notifications.py` — the channel policy. `TRANSACTIONAL`
  ignores `email_enabled`; `OPTIONAL` honours it.

**Wiring:**

- `notify_driver_status_change` now fans out to email for statuses in
  `EMAIL_STATUSES` (`active`, `rejected`, `suspended`, `banned`).
  `needs_review` is excluded — it fires on every vehicle edit and document
  re-upload.
- `admin_verify_driver` routed through the policy, copy byte-identical.
- Document approval that reactivates a driver now calls the policy.
- `document_expiry.py` emails at both branches, and the suspension push moves to
  the `account` tier.

## 4. Risk & impact on existing functionality

**Blast radius: backend only, and additive at every wiring point.**

Grepped before each change:

- `notify_driver_status_change` — 5 call sites: `routes/admin/drivers.py`
  (`/action`, `/status-override`, and now `/verify`),
  `routes/drivers/profile.py`, `documents.py`, and now
  `routes/admin/documents.py`. All get the email fan-out automatically; none
  needed a signature change.
- `send_push_notification` — 40+ callers. **Not modified.** Two call sites in
  `document_expiry.py` gained kwargs (`priority`, `target_app`); every other
  caller is byte-for-byte unaffected.
- `send_transactional_email` — 14 existing callers. **Not modified.** The new
  module is an additional caller, not a change to it.
- `app_settings` — one new column. `get_app_settings()` merges schema defaults
  over the row, so the flag reads `true` whether or not migration 286 has run.
- `notification_preferences.email_enabled` — previously read by nothing; now
  read only for `OPTIONAL`-class mail. **No email currently ships as OPTIONAL**,
  so today this changes no delivery; it is the mechanism, wired ahead of use.

**What could regress:**

- **Notification volume up.** Six driver events that previously sent nothing by
  email now send. Bounded: no state change, no money, no ride flow.
- **`account` tier on expiry suspension is a consent-posture change.** A driver
  who opted out of push will now receive the suspension notice on their device.
  Deliberate — it mirrors the decision already taken on 2026-07-30 for
  admin-initiated suspensions, for the same reason (a driver who can no longer
  earn must be told rather than discovering it as a 403). It also means those
  notices now enter `push_retry_queue`, which requires **migration 272** to have
  been applied; per the 2026-07-30 log that migration had not been applied
  anywhere at the time of writing. **If 272 has not run, the retry enqueue
  violates the old CHECK.** The immediate send still works, so the failure mode
  is quiet — the notice is lost only when the first send fails.
- **Test patch targets moved.** `admin_verify_driver`'s send moved out of
  `routes.admin.drivers` into the policy module, so two tests patching
  `routes.admin.drivers.send_push_notification` stopped intercepting anything.
  Repointed at `routes.admin.drivers.notify_driver_status_change`, with an
  assertion pinning the copy. Same class of breakage as M2 in the 2026-07-30 log.
- **New public route.** `/api/v1/branding/` is App-Check exempt because a mail
  client cannot attach that header. It serves one hardcoded file, is rate
  limited (120/min), and carries no PII and no per-user dimension. A regression
  test asserts the exemption list contains no `/static` prefix.
- **One extra DB read per email sent.** `resolve_recipient` loads the `users`
  row so the copy can greet by first name. It runs only for recipients that
  *won* the claim — i.e. per notification actually sent, not per driver scanned
  — so on the 12-hourly expiry loop that is a handful of reads, not a scan-sized
  N+1. No request-path SLA is affected: none of these run in a request handler
  except the admin verify/approve endpoints, which are already doing several
  writes.

Not touched: ride state machine, dispatch, fare/settlement, wallet, insurance
periods, surge, or any money movement.

## 5. User-experience effect

**Driver-facing.** New emails for: approval, rejection (with reason),
suspension (with reason), ban (reason withheld, same rule as the push),
verify/unverify, document approval that reactivates them, and all four
document-expiry tiers. Each carries the Spinr logo, brand red `#FF3B30`, a
what-to-do-next line the push has no room for, and a plain-text alternative.

**Visible mid-session:** yes. A driver online when an admin suspends them, or
when the 12-hourly expiry loop suspends them, receives both the push and the
email immediately.

**Newly non-silent:** document approval (was nothing), and expiry suspension for
a driver who had opted out of push (was nothing).

**Admin-facing.** No UI change. `lifecycle_emails_enabled` is settable via the
existing settings surface.

**Rider-facing.** None. No rider-side email was added in this branch.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/routes/branding.py` | New — single-file logo route | Emails need a URL for the logo |
| `backend/core/middleware.py` | `/api/v1/branding/` added to `_APP_CHECK_EXEMPT_PREFIXES` | Mail clients cannot send that header |
| `backend/server.py` | Mount the branding router | — |
| `backend/utils/email_layout.py` | New — shared branded layout, HTML + text | Every email invented its own shell or had none |
| `backend/utils/email_notifications.py` | New — channel policy, classes, kill switch | One place decides what also emails |
| `backend/schemas.py` | `lifecycle_emails_enabled: bool = True` | Flag defaults correctly pre-migration |
| `backend/migrations/286_settings_lifecycle_emails_enabled.sql` | New — the switch column | Turn emails off without a redeploy |
| `backend/utils/driver_status_notifications.py` | `EMAIL_STATUSES`, `_EMAIL_NEXT_STEPS`, `verification_message()`, email fan-out in the sender | D15–D18, D7/D8 |
| `backend/routes/admin/drivers.py` | `/verify` routed through the policy | D7/D8 |
| `backend/routes/admin/documents.py` | Notify on reactivating approval; `logger.debug` → `logger.error` | D5 |
| `backend/utils/document_expiry.py` | Email at both branches; suspension push to `account` tier | D10–D13 |
| `backend/tests/test_branding_route.py` | New — 8 tests | Route + exemption + the SGI-forms guard |
| `backend/tests/test_email_layout.py` | New — 15 tests | First email-appearance tests in the repo |
| `backend/tests/test_email_notifications.py` | New — 20 tests | Class matrix, guards, PII |
| `backend/tests/test_driver_status_email.py` | New — 17 tests | Fan-out, copy, contract |
| `backend/tests/test_admin_document_approval_notify.py` | New — 4 tests | D5 |
| `backend/tests/test_document_expiry_email.py` | New — 12 tests | Both channels + replay safety |
| `backend/tests/test_admin_drivers_coverage.py` | 2 tests repointed, copy pinned | Patch target moved |
| `docs/notification-channel-coverage.md` | New — the full audit | Durable record of what is and isn't covered |

## 7. Before / after

```python
# Before — approving the last document reactivated the driver silently
                    if drv and drv.get("status") == "needs_review":
                        await db_supabase.update_one(
                            "drivers", {"id": driver_id},
                            {"status": "active", "is_verified": True},
                        )
                except Exception as _exc:
                    logger.debug(f"Could not reset driver {driver_id} status to active: {_exc}")
```

```python
# After
                    if drv and drv.get("status") == "needs_review":
                        await db_supabase.update_one(
                            "drivers", {"id": driver_id},
                            {"status": "active", "is_verified": True},
                        )
                        await notify_driver_status_change(
                            drv, status_message("active"), "document_approved"
                        )
                except Exception:
                    logger.error(
                        "Could not reset driver %s status to active after document approval",
                        driver_id, exc_info=True,
                    )
```

```python
# Before — suspension notice honoured the push opt-out, so an opted-out
# driver learned nothing about being taken offline
                await send_push_notification(
                    user_id,
                    "Account suspended — expired documents",
                    f"Your account has been suspended: {doc_list}. ...",
                    data={"type": "document_expired_suspension", ...},
                )
```

```python
# After — account tier (bypasses opt-out, retry-queued) + email
                await send_push_notification(
                    user_id, _suspend_title, _suspend_body,
                    data={"type": "document_expired_suspension", ...},
                    priority=ACCOUNT_PRIORITY,
                    target_app="driver",
                )
            await _email_expiry_notice(...)   # inside the same suspension claim
```

## 8. Rollback plan

**Feature-flagged.** `app_settings.lifecycle_emails_enabled = false` suppresses
every email added here, without a redeploy and without touching push. That is
the first move for anything email-related going wrong — wrong copy, unexpected
volume, provider trouble.

| Scenario | Action |
|---|---|
| Emails wrong / too noisy | Set `lifecycle_emails_enabled = false` in admin settings. Takes effect within the 60 s settings cache TTL |
| Logo route problematic | `git revert 1c32fde`. Emails then render the styled alt text instead of the image — degraded, not broken |
| `account` tier on expiry suspension misbehaving | `git revert ad45909` reverts the tier and the expiry emails together |
| Migration 286 | `ALTER TABLE public.settings DROP COLUMN IF EXISTS lifecycle_emails_enabled;` — safe at any time, since `schemas.AppSettings` keeps the code reading `true` |

The flag is checked once inside `send_lifecycle_email`, so it is a single
seam covering all six wired events.

## 9. Verification performed

- [x] **Targeted sweep** — `-k "email or notification or document or
      driver_status or admin_drivers or branding or receipt"`:
      **862 passed, 1 skipped, 0 failed**. 76 of those are new, across 6 new files
- [x] **Full backend suite** — `pytest --ignore=tests/perf`:
      **10 038 passed, 8 skipped, 1 xfailed, 0 failed** (8 m 17 s)
- [x] `ruff check` and `ruff format` clean on every changed file
- [x] **Blast-radius grep** — `notify_driver_status_change`,
      `send_push_notification`, `send_transactional_email`, `email_enabled`,
      `_APP_CHECK_EXEMPT_PREFIXES` callers, all listed in §4
- [x] **Replay safety** — tests drive a lost claim on both expiry branches and
      assert neither channel fires, so the email cannot double-send across the
      18 concurrent background-loop replicas
- [x] **PIPEDA** — the recipient address never reaches logs; `email_provider`
      takes the user id as its redacted `log_id`, asserted by test
- [x] **Injection** — admin-authored rejection/suspension reasons flow into
      email bodies; `email_layout` escapes every caller value, asserted by test
- [x] **Logo route end-to-end through the real app** (`TestClient(server.app)`,
      full middleware stack): `GET /api/v1/branding/spinr-logo.png` → `200`,
      `image/png`, `Cache-Control: public, max-age=31536000, immutable`, 95 391
      bytes of real PNG (the actual asset, not a placeholder), no App Check
      challenge. Confirmed alongside it that `/static/sgi_forms/...`,
      `/static/branding/...` and a traversal attempt under the branding prefix
      all return `404` — the SGI regulator templates stay unreachable, which is
      the whole reason this is a single-file route
- [ ] **Feature-flagged** — yes, see §8
- [ ] **Manual repro in staging** — not done, see §10

## 10. What was NOT verified

- **No real email was sent.** Every test asserts against a mock at the
  `send_transactional_email` / `send_lifecycle_email` boundary. SES and Resend
  delivery, and how the new layout actually renders in Gmail, Apple Mail and
  Outlook, are **unverified**. The logo route itself is confirmed working (§9),
  but no *mail client* has fetched it — Gmail's image proxy behaviour against
  the new route, and whether the styled alt-text fallback reads correctly when
  a client blocks the image, are both untested against a real client.
- **No automated visual/snapshot regression tooling exists for email in this
  repo.** The branding tests assert that the logo URL, brand colour and footer
  lines are present in the HTML — they cannot catch a layout that renders badly.
  This is a standing gap, not something this change closes.
- **Migration 286 has not been applied anywhere** — not staging, not a local
  Postgres. Written from the migration conventions and reviewed by reading only.
  Low risk: the schema default means the code behaves correctly without it; the
  only thing that does not work until it runs is flipping the switch.
- **Migration 272 is a dependency and its status is unconfirmed.** The
  2026-07-30 log records it as not applied anywhere. Raising the expiry
  suspension to the `account` tier means those notices enter
  `push_retry_queue`; without 272 the enqueue violates the old CHECK. **Confirm
  272 is applied before deploying this.**
- **Not run against live or staging Supabase.**
- **The `OPTIONAL` email class is untested in production** because nothing ships
  as OPTIONAL yet. Its behaviour is covered by unit tests only.
- **New customer-facing copy has had no product or copy review.** The
  next-step lines ("Upload the renewed document in the Spinr driver app under
  Profile → Documents…") were written in this session.
- **No production build run** — backend-only change, no frontend surface
  touched. Phase 2 makes the existing rider-app "Email Notifications" toggle
  meaningful for OPTIONAL mail but does not modify `rider-app/`.
