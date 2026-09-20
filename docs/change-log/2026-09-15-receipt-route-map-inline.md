# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-09-15 |
| Author | Cursor Grok |
| Surface(s) | backend |
| Domain (Sentry tag) | rides |
| PR / commit link | uncommitted |
| Related issue or gap ID | Owner report — rider receipt email has a PNG attached instead of the map in the body and PDF |

## 1. Issue / gap identified

The completed-ride receipt email arrives with a downloadable `Spinr-route-*.png` attachment. The HTML body does not show the route map, so the rider has to open the PNG (and hope the PDF has it) to see the trip.

## 2. Root cause

Private Storage snapshot URLs expire, so `send_receipt_email_result` deliberately set `include_route_snapshot=False` and attached the downloaded PNG as a normal `multipart/mixed` file. `_build_mime` had no `multipart/related` / CID support (the branding logo route's docstring recorded that gap). The PDF path already received the same bytes; the emailed HTML did not.

## 3. Fix / remediation

Download the snapshot while the signed URL is still valid, embed those bytes in the PDF (unchanged generator), and CID-inline the same PNG in the HTML body (`<img src="cid:spinr-route-snapshot">`) via `multipart/related`. The PNG is no longer a downloadable attachment. SES MIME and Resend's `content_id` field both carry the inline part.

Alternative considered: keep the PNG as a file attachment (status quo — the reported bug). A public unauthenticated URL for the map was rejected (PIPEDA: it is a trip GPS trace). A `data:` URI was rejected (Gmail strips them). CID-related inline wins because the bytes travel with the message and render in the body.

## 4. Risk & impact on existing functionality

- **What else reads/writes the same paths:** `send_transactional_email` / `_build_mime` / `_try_resend` are shared. Blast-radius grep for `attachments=` found: `email_receipt.py` (this change), `features.py` (passthrough), `routes/admin/driver_statements.py`, `routes/drivers/subscriptions.py`, `routes/drivers/tax_exports.py`, `utils/driver_statement_job.py`. None of those pass `content_id`, so they stay `multipart/mixed` file attachments. Receipts are the only caller that now pass `content_id`.
- **Could this regress a working flow?** Other transactional emails with PDF/ZIP attachments should be unchanged. Receipts lose the paperclip PNG and gain an in-body map. Fare line items, totals, and tax rows are untouched.
- **Blast radius:** single-surface (backend email + PDF receipt). No ride state machine, wallet, or background-loop change. The outbox still calls `send_receipt_email_result`; only the MIME it builds changes.
- **Logo emails:** still hotlink `/api/v1/branding/spinr-logo.png`. CID is used only for the private route snapshot.

## 5. User-experience effect

- **Who sees it:** riders who receive a ride receipt email (and anyone an admin resends a receipt to). Not mid-ride.
- **Visible change:** the route map appears in the email body; the extra PNG file is gone; the PDF attachment remains and still embeds the map. Copy "A permanent map copy is attached to this receipt." is removed.
- rider-app / driver-app have no visual-regression tooling; this is email, not those apps. No Litmus/Email-on-Acid check was run (N12 still open).

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `backend/utils/email_provider.py` | `multipart/related` + CID inline parts; Resend `content_id` / `content_type` | So the map can live in the HTML body |
| `backend/utils/email_receipt.py` | CID `img src`; PNG sent with `content_id`; dropped downloadable-PNG path | Stop attaching a file the rider has to open |
| `backend/utils/receipt_pdf.py` | Name the BytesIO `route.png` before `pdf.image` | Help fpdf2 sniff PNG when embedding |
| `backend/routes/branding.py` | Docstring: logo stays a public URL; maps use CID | The old "no multipart/related" claim is no longer true |
| `backend/tests/test_email_provider.py` | CID related/mixed MIME + Resend `content_id` tests | Pin the new MIME contract |
| `backend/tests/test_receipt_route_snapshot.py` | CID HTML + send-path assertions; PDF still gets bytes | Pin body + PDF, not a PNG paperclip |
| `docs/change-log/2026-09-15-receipt-route-map-inline.md` | This log | Live-tested receipt surface |

## 7. Before / after

```python
# Before — HTML omitted the map; PNG was a downloadable attachment
html = generate_receipt_html(..., include_route_snapshot=False, ...)
attachments.append({"filename": f"Spinr-route-{ref}.png", "content": snapshot_bytes, "mime": "image/png"})
```

```python
# After — HTML references a CID; PNG is related-inline; PDF still gets the bytes
html = generate_receipt_html(
    ...,
    include_route_snapshot=False,
    route_snapshot_src=f"cid:{_ROUTE_SNAPSHOT_CID}" if snapshot_bytes else None,
    ...
)
attachments.append({..., "content_id": _ROUTE_SNAPSHOT_CID})
```

## 8. Rollback plan

No feature flag and no data migration — this is MIME/HTML construction only. Already-sent emails cannot be rewritten. Rollback is a backend redeploy of the previous `email_provider.py` / `email_receipt.py`. Receipts sent after that revert go back to a downloadable PNG and no in-body map. No Stripe/wallet/ride-state remediation.

Not flagged: a dual MIME shape in production would mean some riders get a paperclip PNG and some get CID, which is worse than one delivery format. The change is not a new product surface.

## 9. Verification performed

- [x] Automated tests: `pytest tests/test_email_provider.py tests/test_receipt_route_snapshot.py tests/test_receipt_pdf.py tests/test_receipt_shell_snapshot.py tests/test_email_snapshots.py tests/test_ride_receipt_delivery.py tests/test_outbox_receipts.py tests/test_admin_send_receipt_email.py -p no:xonsh --no-cov` → 105 passed, then `test_email_receipt_omits_cid_when_snapshot_download_fails` added and passed
- [x] `ruff check` on the touched backend files → clean
- [ ] Manual repro in a real inbox (Gmail / Outlook / Apple Mail) — not done
- [x] Blast-radius grep: `attachments=`, `Spinr-route-`, `include_route_snapshot`, `content_id`, `permanent map copy`
- [x] PIPEDA: map is not published at a public URL; CID keeps the GPS trace inside the recipient's message. Fare/tax math untouched.
- [x] Not feature-flagged — see §8

## 10. What was NOT verified

- No real inbox send. CID inline rendering is client-specific; Gmail sometimes still lists related images in the attachment tray even with `Content-Disposition: inline`.
- PDF embedding against a production-sized Google Static Maps PNG was not re-run beyond the existing unit test (`test_pdf_embeds_snapshot_bytes_and_prints_truthful_quality_note`).
- No `npm run build` — backend-only.

## 11. Sign-off

- [x] Rollback plan is a backend redeploy (no live-data rewrite)
- [x] Blast radius stated (shared email MIME builder; only receipts pass `content_id`)
- [x] UX field filled in (in-body map, no PNG paperclip)

## 12. Security-review follow-up

- Removed leftover `#region agent log` writes to `debug-5a2fb8.log` on the driver location hot path.
- `_cid_token` now rejects empty/control/quoted values; invalid CIDs skip the inline part instead of dropping the send.
- `route_snapshot_src` is documented and runtime-restricted to `cid:<token>`; HTML `src`/`alt` are escaped.
