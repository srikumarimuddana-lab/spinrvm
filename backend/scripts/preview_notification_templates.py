#!/usr/bin/env python3
"""Render sample copies of driver/rider emails, SMS, and push notices to PDF.

Dev/QA tool only — not part of the production runtime, not imported by any
route or test. Built to let an operator eyeball the FORMAT and BODY COPY of
every driver- and rider-facing notification before/around live app testing,
without touching real user data or the database.

Produces two files in --out-dir:
  driver-emails-preview.{html,pdf}          — every email a driver receives
  rider-driver-messages-preview.{html,pdf}  — every email/SMS/push either
                                               a rider or a driver receives

Every value shown is SAMPLE data (fake name, fake amounts, fake codes) — this
is a template/format check, not a report of real sends. Sample data lives
inline in this file's *_ITEMS lists below, next to a `source` string naming
the real function it mirrors.

Why this duplicates rather than imports backend code
------------------------------------------------------
The real rendering lives in utils/email_layout.py (render_email /
render_from_text), and the real copy lives in utils/driver_emails.py,
utils/rider_emails.py, utils/driver_status_notifications.py,
utils/document_expiry.py, sms_service.py, utils/marketing_sms.py,
utils/driver_statement_job.py, and routes/admin/messaging.py. Importing those
modules pulls in the full backend dependency chain (pydantic-settings, bcrypt,
Supabase client, ...), which this environment cannot install (no PyPI
network access) and which a template preview has no real reason to need.

So the HTML-rendering helpers below are a byte-for-byte copy of
utils/email_layout.py's private helpers (as of 2026-08-28), and each *_ITEMS
entry's heading/paragraphs/subject text is copied from the real sender
function named in its `source` field. THIS FILE IS NOT AUTO-SYNCED — if the
real copy or layout changes, re-copy it here by hand. Treat any mismatch
against a from-memory expectation as this script being stale, not the other
way around.

Usage:
    python3 backend/scripts/preview_notification_templates.py [--out-dir DIR]

Needs only the Python standard library. PDF conversion additionally shells
out to a local headless Chromium/Chrome binary if one can be found (checked
via $CHROME_PATH, then $PLAYWRIGHT_BROWSERS_PATH, then common install paths);
if none is found, the HTML files are still written and the script says so.
"""

from __future__ import annotations

import argparse
import base64
import glob
import html
import os
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
LOGO_PATH = REPO_ROOT / "backend" / "static" / "branding" / "spinr_logo.png"

# ── Brand tokens — copied from utils/email_layout.py ────────────────────────
BRAND_RED = "#FF3B30"
BRAND_RED_CONTRAST = "#D32F2F"
INK = "#1A1A1A"
MUTED = "#6B7280"
SURFACE = "#FFFFFF"
PAGE_BG = "#F0F0F0"
HEADER_BG = "#F2F2F2"
HEADER_META = "#6B6B6B"
FOOTER_BG = "#101010"
FOOTER_NAME = "#FFFFFF"
FOOTER_TEXT = "#A6A6A6"
FONT_STACK = "'Plus Jakarta Sans',-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
_MAX_WIDTH_PX = 600
_LOGO_WIDTH_PX = 132
_PAD_X = 40

COMPANY_LINE = "Spinr Mobility Inc. - Saskatoon, SK"
COMPANY_CONTACT_LINE = "support@spinr.ca - www.spinr.ca"


def _logo_data_uri() -> str:
    """Embed the real logo asset inline so the preview needs no network."""
    if not LOGO_PATH.is_file():
        return ""
    b64 = base64.b64encode(LOGO_PATH.read_bytes()).decode("ascii")
    return f"data:image/png;base64,{b64}"


class Company:
    """Stand-in for utils.company_details.CompanyDetails — sample values only."""

    name = "Spinr Mobility Inc."
    app_name = "Spinr"
    identity_line = COMPANY_LINE
    contact_line = COMPANY_CONTACT_LINE
    support_email = "support@spinr.ca"
    logo_url = _logo_data_uri()
    address_lines = ("123 2nd Ave N", "Saskatoon, SK  S7K 2C6")
    app_download_url = "https://spinr.ca/app"


COMPANY = Company()

# ── HTML helpers — copied from utils/email_layout.py ────────────────────────


def _esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _esc_multiline(value: object) -> str:
    escaped = _esc(value).replace("\n", "<br>")
    return re.sub(r"  +", lambda m: "&nbsp;" * len(m.group()), escaped)


def _header_html(company, subtitle=None, *, heading=None, intro=None, meta_lines=()):
    meta = "".join(
        f'<div style="color:{HEADER_META};font-size:12px;line-height:18px;">{_esc(line)}</div>'
        for line in meta_lines
        if line
    )
    blocks = [
        f"""
        <tr><td style="background:{HEADER_BG};padding:28px {_PAD_X}px 0;">
          <table width="100%" cellpadding="0" cellspacing="0" role="presentation">
            <tr>
              <td align="left" style="vertical-align:middle;">
                <img src="{_esc(company.logo_url)}" alt="{_esc(company.name)}" width="{_LOGO_WIDTH_PX}"
                     style="display:block;border:0;outline:none;text-decoration:none;
                            width:{_LOGO_WIDTH_PX}px;max-width:100%;height:auto;"/>
              </td>
              <td align="right" style="vertical-align:top;text-align:right;">{meta}</td>
            </tr>
          </table>
        </td></tr>"""
    ]
    if subtitle:
        blocks.append(
            f'<tr><td style="background:{HEADER_BG};padding:24px {_PAD_X}px 0;">'
            f'<div style="color:{HEADER_META};font-size:12px;font-weight:600;'
            f'letter-spacing:0.08em;text-transform:uppercase;">{_esc(subtitle)}</div></td></tr>'
        )
    if heading:
        pad_top = "8px" if subtitle else "28px"
        blocks.append(
            f'<tr><td style="background:{HEADER_BG};padding:{pad_top} {_PAD_X}px 0;">'
            f'<h1 style="color:{INK};font-size:30px;line-height:38px;font-weight:700;'
            f'margin:0;letter-spacing:-0.5px;">{_esc(heading)}</h1></td></tr>'
        )
    if intro:
        blocks.append(
            f'<tr><td style="background:{HEADER_BG};padding:12px {_PAD_X}px 0;">'
            f'<div style="color:{MUTED};font-size:16px;line-height:26px;">{_esc(intro)}</div></td></tr>'
        )
    blocks.append(f'<tr><td style="background:{HEADER_BG};height:32px;font-size:0;line-height:0;">&nbsp;</td></tr>')
    return "".join(blocks)


def _brand_rule_html():
    return f'<tr><td style="background:{BRAND_RED};height:4px;font-size:0;line-height:0;">&nbsp;</td></tr>'


def _body_html(greeting, paragraphs, cta, footnote):
    parts = []
    first_pad = "32px"
    if greeting:
        parts.append(
            f'<tr><td style="padding:{first_pad} {_PAD_X}px 0;">'
            f'<p style="color:{INK};font-size:16px;line-height:24px;margin:0;">{_esc(greeting)}</p></td></tr>'
        )
        first_pad = "16px"
    for para in paragraphs:
        parts.append(
            f'<tr><td style="padding:{first_pad} {_PAD_X}px 0;">'
            f'<p style="color:{MUTED};font-size:15px;line-height:24px;margin:0;">{_esc_multiline(para)}</p></td></tr>'
        )
        first_pad = "14px"
    if cta:
        label, url = cta
        parts.append(
            f'<tr><td style="padding:28px {_PAD_X}px 0;">'
            f'<a href="{_esc(url)}" style="display:inline-block;background:{BRAND_RED_CONTRAST};'
            f"color:#ffffff;text-decoration:none;font-size:15px;font-weight:600;"
            f'padding:13px 28px;border-radius:8px;">{_esc(label)}</a></td></tr>'
        )
    if footnote:
        parts.append(
            f'<tr><td style="padding:24px {_PAD_X}px 0;">'
            f'<p style="color:{MUTED};font-size:12px;line-height:18px;margin:0;">{_esc(footnote)}</p></td></tr>'
        )
    parts.append('<tr><td style="height:32px;font-size:0;line-height:0;">&nbsp;</td></tr>')
    return "".join(parts)


def _footer_html(company):
    address = "".join(
        f'<div style="color:{FOOTER_TEXT};font-size:13px;line-height:20px;">{_esc(line)}</div>'
        for line in company.address_lines
    )
    contact = (
        f'<div style="color:{FOOTER_TEXT};font-size:13px;line-height:20px;padding-top:12px;">'
        f"{_esc(company.contact_line)}</div>"
    )
    return f"""
        <tr><td style="background:{FOOTER_BG};padding:28px {_PAD_X}px;">
          <div style="color:{FOOTER_NAME};font-size:14px;line-height:22px;font-weight:600;">{_esc(company.name)}</div>
          {address}
          {contact}
        </td></tr>"""


def email_card_html(
    *, heading=None, paragraphs=(), greeting=None, subtitle=None, cta=None, footnote=None, meta_lines=()
) -> str:
    """The branded email card, as a fragment (no <html>/<head>) for embedding
    multiple previews on one printable page. Mirrors render_email's markup."""
    paras = [p for p in paragraphs if p]
    return f"""<table width="100%" cellpadding="0" cellspacing="0" role="presentation"
       style="max-width:{_MAX_WIDTH_PX}px;background:{SURFACE};border-radius:14px;overflow:hidden;margin:0 auto;font-family:{FONT_STACK};">
{_brand_rule_html()}
{_header_html(COMPANY, subtitle, heading=heading, meta_lines=meta_lines)}
{_body_html(greeting, paras, cta, footnote)}
{_footer_html(COMPANY)}
</table>"""


def render_from_text_card_html(*, heading: str, body: str) -> str:
    """Mirrors utils/email_layout.render_from_text: splits on blank lines."""
    paragraphs = [p.strip() for p in (body or "").split("\n\n") if p.strip()]
    return email_card_html(heading=heading, paragraphs=paragraphs)


# ── Sample content ───────────────────────────────────────────────────────────
# Each item: source = real function this mirrors; trigger = when it fires;
# the rest = the real copy (with placeholder name/amount/code substituted).

SAMPLE_NAME = "Jordan"
SAMPLE_SUPPORT = COMPANY.support_email
SAMPLE_APP = COMPANY.app_name

DRIVER_EMAIL_ITEMS = [
    dict(
        email_type="driver_welcome",
        trigger="Driver application row created (registration)",
        source="utils/driver_emails.py::send_driver_welcome_email",
        subject=f"You're in — let's get your {SAMPLE_APP} account approved",
        html=email_card_html(
            greeting=f"Hi {SAMPLE_NAME},",
            heading=f"Welcome to {SAMPLE_APP} driving",
            paragraphs=[
                f"Thanks for signing up to drive with {SAMPLE_APP}. Your account is created — "
                "here's what happens next.",
                "Upload your driver's licence, vehicle insurance, vehicle inspection, and background "
                f"check in the {SAMPLE_APP} driver app if you haven't already — we'll notify you "
                "as soon as they've been reviewed.",
                "Complete your driver training at training.spinr.ca before your first ride.",
                f"{SAMPLE_APP} takes 0% commission — apart from fees and taxes, the trip fare "
                "is 100% yours. No per-trip cut, ever.",
                "Check the Subscription screen in the driver app for your area's current Spinr Pass "
                "status — some areas have no subscription fee right now.",
                "While you wait, finish your vehicle details in the app so there's nothing left to do "
                "once you're approved.",
                f"Questions any time: {SAMPLE_SUPPORT}",
            ],
            footnote=f"Didn't sign up for a {SAMPLE_APP} driver account? Contact {SAMPLE_SUPPORT} and we'll close it.",
        ),
    ),
]

# driver_status_notifications.py — EMAIL_STATUSES = {active, rejected, suspended, banned}
_STATUS_EMAIL_COPY = [
    (
        "driver_active",
        "active",
        "You're Approved! \U0001f389",
        "Your driver account is active. You can now go online and start earning!",
        f"Open the {SAMPLE_APP} driver app, tap Go Online, and you'll start receiving ride offers.",
    ),
    (
        "driver_rejected",
        "rejected",
        "Application Update",
        "Your driver application needs attention. Please check your documents.",
        f"Open the {SAMPLE_APP} driver app to review your documents and submit them again. "
        f"If you think this decision is wrong, contact {SAMPLE_SUPPORT}.",
    ),
    (
        "driver_suspended",
        "suspended",
        "Account Suspended ⚠️",
        # _with_reason() appends "Reason: {reason}" when a reason is given (always
        # true for this action — routes/admin/drivers.py requires it) and ONLY
        # appends "Contact support for details." in the no-reason branch, which
        # the primary suspend action can never hit. The two never appear together.
        "Your account has been suspended. Reason: Expired vehicle insurance.",
        f"You won't be able to go online while your account is suspended. Contact {SAMPLE_SUPPORT} "
        "if you have questions or want to appeal.",
    ),
    (
        "driver_banned",
        "banned",
        "Account Deactivated",
        "Your driver account has been deactivated. Contact support for more information.",
        f"Contact {SAMPLE_SUPPORT} if you'd like more information about this decision.",
    ),
]
for _dtype, _status, _title, _body, _next in _STATUS_EMAIL_COPY:
    DRIVER_EMAIL_ITEMS.append(
        dict(
            email_type=_dtype,
            trigger=f"Driver enters status '{_status}' (admin action or self-service transition)",
            source="utils/driver_status_notifications.py::_send_status_email",
            subject=_title,
            html=email_card_html(greeting=f"Hi {SAMPLE_NAME},", heading=_title, paragraphs=[_body, _next]),
        )
    )

_VERIFY_COPY = [
    (
        "driver_verified",
        True,
        "Account Verified! ✅",
        "Your driver account has been verified. You can now go online and start accepting rides!",
        f"Open the {SAMPLE_APP} driver app, tap Go Online, and you'll start receiving ride offers.",
    ),
    (
        "driver_unverified",
        False,
        "Verification Update ⚠️",
        "Your driver verification status has been updated. Please check your documents.",
        f"Open the {SAMPLE_APP} driver app to check your documents. Contact {SAMPLE_SUPPORT} "
        "if you're not sure what needs updating.",
    ),
]
for _dtype, _verified, _title, _body, _next in _VERIFY_COPY:
    DRIVER_EMAIL_ITEMS.append(
        dict(
            email_type=_dtype,
            trigger=f"Admin toggles driver verified={_verified}",
            source="utils/driver_status_notifications.py::verification_message",
            subject=_title,
            html=email_card_html(greeting=f"Hi {SAMPLE_NAME},", heading=_title, paragraphs=[_body, _next]),
        )
    )

DRIVER_EMAIL_ITEMS += [
    dict(
        email_type="document_expiry_warning",
        trigger="Document expiring within 7 days (12h background sweep)",
        source="utils/document_expiry.py::_email_expiry_notice",
        subject="Document expiring in 5 days",
        html=email_card_html(
            greeting=f"Hi {SAMPLE_NAME},",
            heading="Document expiring in 5 days",
            paragraphs=[
                "Please renew: Driver's License. You won't be able to go online with expired documents.",
                f"Upload the renewed document in the {SAMPLE_APP} driver app under Profile → Documents. "
                "You'll stay online as long as it's approved before the expiry date.",
            ],
        ),
    ),
    dict(
        email_type="document_expired_suspension",
        trigger="Document already expired → driver auto-suspended",
        source="utils/document_expiry.py::_email_expiry_notice",
        subject="Account suspended — expired documents",
        html=email_card_html(
            greeting=f"Hi {SAMPLE_NAME},",
            heading="Account suspended — expired documents",
            paragraphs=[
                "Your account has been suspended: Vehicle Insurance. Please renew to continue driving.",
                f"Upload a current copy in the {SAMPLE_APP} driver app under Profile → Documents. "
                "Your account is restored once an admin approves it.",
            ],
        ),
    ),
    dict(
        email_type="transactional (driver statement)",
        trigger="Weekly/monthly earnings statement job",
        source="utils/driver_statement_job.py::_send + features.send_email",
        subject="Your Spinr weekly earnings statement — Aug 18–24, 2026",
        html=render_from_text_card_html(
            heading="Your Spinr weekly earnings statement — Aug 18–24, 2026",
            body=(
                "Hi,\n\n"
                "Your Spinr weekly earnings statement for Aug 18–24, 2026 is attached.\n\n"
                "  Total earnings: $612.40\n"
                "  Trips completed: 38\n"
                "  Paid out this period: $612.40\n\n"
                "The attached PDF has the full breakdown. You keep 100% of every "
                "fare — Spinr takes no commission.\n\n"
                f"Questions? Reply to {SAMPLE_SUPPORT}.\n\n"
                "— The Spinr Team"
            ),
        ),
    ),
    dict(
        email_type="broadcast",
        trigger="Admin sends a Cloud Message to audience=drivers, channel=email",
        source="routes/admin/messaging.py::_send_email_one (admin-authored text, sample below)",
        subject="Surge pricing is active in your area",
        html=render_from_text_card_html(
            heading="Surge pricing is active in your area",
            body=(
                "Demand is high in Saskatoon Downtown right now — riders are seeing higher fares, "
                "which means more for you on every trip.\n\n"
                "Go online now to catch it while it lasts."
            ),
        ),
    ),
]

RIDER_EMAIL_ITEMS = [
    dict(
        email_type="rider_welcome",
        trigger="Rider completes profile setup",
        source="utils/rider_emails.py::send_welcome_email",
        subject=f"Welcome to {SAMPLE_APP} — drivers keep 100% of your fare",
        html=email_card_html(
            greeting=f"Hi {SAMPLE_NAME},",
            heading=f"Welcome to {SAMPLE_APP}",
            paragraphs=[
                f"Your account is ready. Open the {SAMPLE_APP} app, set a pickup and drop-off, "
                "and you'll see the full price before you confirm — no surprises at drop-off.",
                f"{SAMPLE_APP} is Canadian-built and takes 0% commission — every dollar of the fare "
                "goes to your driver. GST and PST are shown as separate line items on your receipt.",
                f"Questions any time: {SAMPLE_SUPPORT}",
            ],
            cta=("Book your first ride", COMPANY.app_download_url),
        ),
    ),
    dict(
        email_type="rider_email_changed",
        trigger="Rider changes their account email (sent to the OLD address)",
        source="utils/rider_emails.py::send_email_changed_notice",
        subject=f"The email on your {SAMPLE_APP} account was changed",
        html=email_card_html(
            greeting=f"Hi {SAMPLE_NAME},",
            heading=f"Your {SAMPLE_APP} email address was changed",
            paragraphs=[
                f"The email address on your {SAMPLE_APP} account was just changed, so this is the last "
                "message this address will receive.",
                f"If you made this change, nothing more is needed. If you did not, contact "
                f"{SAMPLE_SUPPORT} straight away — your account may have been accessed by someone else.",
            ],
        ),
    ),
    dict(
        email_type="rider_new_device_signin",
        trigger="Sign-in from a device fingerprint not seen before",
        source="utils/rider_emails.py::send_new_device_notice",
        subject=f"New sign-in to your {SAMPLE_APP} account",
        html=email_card_html(
            greeting=f"Hi {SAMPLE_NAME},",
            heading="New sign-in to your account",
            paragraphs=[
                f"Your {SAMPLE_APP} account was just signed into from a device we haven't seen before.",
                "If this was you, no action is needed. If you don't recognize this sign-in, contact "
                f"{SAMPLE_SUPPORT} immediately.",
            ],
        ),
    ),
    dict(
        email_type="rider_account_deletion",
        trigger="PIPEDA deletion request processed",
        source="utils/rider_emails.py::send_account_deletion_notice",
        subject=f"Your {SAMPLE_APP} account has been deactivated",
        html=email_card_html(
            greeting=f"Hi {SAMPLE_NAME},",
            heading="Your account has been deactivated",
            paragraphs=[
                f"We've received your deletion request and your {SAMPLE_APP} account is now closed. "
                "You can reactivate it any time by signing in with your phone number.",
                "Your ride records are kept, still linked to you, because the Saskatchewan "
                "Transportation Act and Canadian tax rules require us to hold them for seven "
                "years. After that they are permanently deleted — currently scheduled for 2033-08-28. "
                "Location traces are removed sooner, at three years.",
                f"If you did not request this, contact {SAMPLE_SUPPORT} immediately.",
            ],
        ),
    ),
    dict(
        email_type="rider_refund",
        trigger="Refund issued for a ride",
        source="utils/rider_emails.py::send_refund_email",
        subject=f"Your {SAMPLE_APP} refund of $12.40",
        html=email_card_html(
            greeting=f"Hi {SAMPLE_NAME},",
            heading="Refund processed — $12.40 CAD",
            paragraphs=[
                "We've refunded $12.40 CAD for ride A1B2C3D4. Your bank has processed it from our side.",
                "Depending on your bank, it can take 5–10 business days to appear on your "
                "statement. It will be credited to the original payment method.",
            ],
            footnote=f"Not expecting this refund? Contact {SAMPLE_SUPPORT}.",
        ),
    ),
    dict(
        email_type="rider_wallet_topup",
        trigger="Rider wallet top-up completes",
        source="utils/rider_emails.py::send_wallet_topup_email",
        subject=f"{SAMPLE_APP} wallet top-up — $50.00",
        html=email_card_html(
            greeting=f"Hi {SAMPLE_NAME},",
            heading="Wallet topped up — $50.00 CAD",
            paragraphs=[
                f"$50.00 CAD has been added to your {SAMPLE_APP} wallet.",
                "Your wallet balance is now $63.75 CAD.",
                "Your wallet is used automatically on your next ride unless you pick another payment method.",
            ],
            footnote=f"Didn't make this top-up? Contact {SAMPLE_SUPPORT}.",
        ),
    ),
    dict(
        email_type="rider_no_show_fee",
        trigger="Driver waits at pickup, ride doesn't start",
        source="utils/rider_emails.py::send_no_show_fee_email",
        subject="No-show fee charged — $8.00",
        html=email_card_html(
            greeting=f"Hi {SAMPLE_NAME},",
            heading="A $8.00 no-show fee was charged",
            paragraphs=[
                "Your driver arrived and waited at the pickup point, but the ride didn't start, "
                "so a no-show fee of $8.00 CAD was charged for ride A1B2C3D4.",
                "The fee goes to the driver for the time and distance they spent getting to you.",
            ],
            footnote=f"Think this is wrong? Contact {SAMPLE_SUPPORT} and we'll review it.",
        ),
    ),
    dict(
        email_type="rider_payment_blocked",
        trigger="Payment retries exhausted; rider can no longer book",
        source="utils/rider_emails.py::send_payment_blocked_email",
        subject=f"Action needed: your {SAMPLE_APP} payment didn't go through",
        html=email_card_html(
            greeting=f"Hi {SAMPLE_NAME},",
            heading="We couldn't complete your payment",
            paragraphs=[
                "We tried several times to charge $18.90 CAD for ride A1B2C3D4, "
                "and your payment method declined each time.",
                f"You won't be able to book another ride until this is settled. Open the {SAMPLE_APP} app "
                "and add or update a payment method, and we'll retry the outstanding amount.",
                f"If you think your card is fine, contact {SAMPLE_SUPPORT} and we'll look into it.",
            ],
        ),
    ),
    dict(
        email_type="rider_email_verification",
        trigger="Rider requests self-serve email verification",
        source="utils/rider_emails.py::send_email_verification_code",
        subject="Your Spinr verification code",
        html=email_card_html(
            greeting=f"Hi {SAMPLE_NAME},",
            heading="Verify your email",
            paragraphs=[
                "Your Spinr verification code is 482913.",
                "It expires in 15 minutes. If you didn't request it, you can ignore this email.",
            ],
        ),
    ),
    dict(
        email_type="marketing",
        trigger="Admin-authored marketing send (CASL: consent-gated, unsubscribe required)",
        source="utils/marketing_email.py::send_marketing_email (CASL footer appended below the card)",
        subject="20% off your next 3 rides this week",
        html=render_from_text_card_html(
            heading="20% off your next 3 rides this week",
            body=(
                "Book any ride before Sunday and get 20% off, up to $5 per trip, on your next three rides."
                "\n\n---\nYou are receiving this because you opted in to marketing from Spinr Mobility Inc."
                "\nSpinr Mobility Inc.\n123 2nd Ave N, Saskatoon, SK  S7K 2C6"
                "\nUnsubscribe: https://api.spinr.ca/api/v1/marketing/unsubscribe?token=..."
            ),
        ),
    ),
    dict(
        email_type="broadcast",
        trigger="Admin sends a Cloud Message to audience=customers, channel=email",
        source="routes/admin/messaging.py::_send_email_one (admin-authored text, sample below)",
        subject="Scheduled maintenance tonight",
        html=render_from_text_card_html(
            heading="Scheduled maintenance tonight",
            body=(
                "The Spinr app will be briefly unavailable between 2:00–2:15 AM CST tonight for scheduled "
                "maintenance. Booking will resume automatically — no action needed."
            ),
        ),
    ),
]

# ── SMS + push samples (no HTML shell — shown as plain-text message cards) ──

SMS_ITEMS = [
    dict(
        channel="SMS",
        audience="Rider & Driver",
        email_type="otp",
        trigger="Login / signup OTP request",
        source="sms_service.py::send_otp_sms",
        body="Your Spinr verification code is: 482913. It expires in 5 minutes.",
    ),
    dict(
        channel="SMS",
        audience="Rider or Driver (opted in)",
        email_type="marketing",
        trigger="Admin-authored marketing SMS send (CASL: consent-gated)",
        source="utils/marketing_sms.py::send_marketing_sms",
        body="20% off your next 3 rides this week. Book before Sunday.\nReply STOP to unsubscribe.",
    ),
    dict(
        channel="SMS",
        audience="Rider or Driver (per admin selection)",
        email_type="broadcast",
        trigger="Admin sends a Cloud Message with channel=sms",
        source="routes/admin/messaging.py::_send_sms_one",
        body="Scheduled maintenance tonight\nThe app will be briefly unavailable 2:00-2:15 AM CST tonight.",
    ),
]

PUSH_ITEMS = [
    dict(
        channel="Push",
        audience="Driver",
        email_type="driver_approve",
        trigger="Admin approves a driver application",
        source="utils/driver_status_notifications.py::action_message",
        title="You're Approved! \U0001f389",
        body="Your driver application has been approved. You can now go online and start earning!",
    ),
    dict(
        channel="Push",
        audience="Driver",
        email_type="driver_suspend",
        trigger="Admin suspends a driver",
        source="utils/driver_status_notifications.py::action_message",
        title="Account Suspended ⚠️",
        body="Your account has been suspended. Reason: Expired vehicle insurance. Contact support for details.",
    ),
    dict(
        channel="Push",
        audience="Driver",
        email_type="document_expiry_today",
        trigger="A required document expires today",
        source="utils/document_expiry.py::check_expiring_documents",
        title="Driver's License expires today",
        body="Your Driver's License expires today. Renew now to avoid account suspension.",
    ),
    dict(
        channel="Push",
        audience="Rider or Driver (per admin selection)",
        email_type="broadcast",
        trigger="Admin sends a Cloud Message with channel=push",
        source="routes/admin/messaging.py::_send_push_one",
        title="Surge pricing is active in your area",
        body="Demand is high in Saskatoon Downtown right now.",
    ),
]

# ── Page assembly ─────────────────────────────────────────────────────────────

_PAGE_HEAD = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
  body { margin:0; font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
         background:#e5e5e5; }
  .cover { padding:64px 48px; background:#101010; color:#fff; }
  .cover h1 { font-size:32px; margin:0 0 12px; }
  .cover p { color:#c7c7c7; font-size:14px; line-height:22px; max-width:640px; }
  .item { page-break-after: always; padding: 28px 0 40px; }
  .item:last-child { page-break-after: auto; }
  .meta { max-width:600px; margin:0 auto 14px; background:#fff3cd; border:1px solid #ffe69c;
          border-radius:8px; padding:10px 16px; font-size:12px; color:#664d03; }
  .meta b { color:#40331a; }
  .msgcard { max-width:420px; margin:0 auto; background:#fff; border-radius:14px; padding:20px 22px;
             box-shadow:0 1px 3px rgba(0,0,0,.12); }
  .msgcard .title { font-weight:700; font-size:15px; margin-bottom:6px; color:#1A1A1A; }
  .msgcard .body { font-size:13.5px; line-height:20px; color:#333; white-space:pre-wrap; }
  .msgcard .tag { display:inline-block; font-size:10px; font-weight:700; letter-spacing:.06em;
                  text-transform:uppercase; color:#D32F2F; margin-bottom:8px; }
</style></head><body>
"""
_PAGE_TAIL = "</body></html>"


def _meta_bar(item: dict) -> str:
    parts = [f"<b>type:</b> {_esc(item['email_type'])}"]
    if "channel" in item:
        parts.append(f"<b>channel:</b> {_esc(item['channel'])}")
    if "audience" in item:
        parts.append(f"<b>audience:</b> {_esc(item['audience'])}")
    parts.append(f"<b>trigger:</b> {_esc(item['trigger'])}")
    parts.append(f"<b>mirrors:</b> {_esc(item['source'])}")
    return f'<div class="meta">{" &nbsp;·&nbsp; ".join(parts)}</div>'


def _email_page(item: dict) -> str:
    subj = f'<div class="meta"><b>subject:</b> {_esc(item["subject"])}</div>' if item.get("subject") else ""
    return f'<div class="item">{_meta_bar(item)}{subj}{item["html"]}</div>'


def _message_page(item: dict) -> str:
    title_html = f'<div class="title">{_esc(item["title"])}</div>' if item.get("title") else ""
    return (
        f'<div class="item">{_meta_bar(item)}'
        f'<div class="msgcard"><span class="tag">{_esc(item["channel"])}</span>'
        f'{title_html}<div class="body">{_esc(item["body"])}</div></div></div>'
    )


def _cover(title: str, subtitle: str, count_line: str) -> str:
    return (
        f'<div class="cover"><h1>{_esc(title)}</h1>'
        f"<p>{_esc(subtitle)}</p>"
        f'<p><b style="color:#fff">{_esc(count_line)}</b></p>'
        "<p>Sample data only — fake name, amounts, and codes. Not a report of real sends; "
        "SMS/push have no per-recipient content log in Spinr today (see email_send_log's "
        "PIPEDA-driven design), so this is a format/copy check against current source, "
        "not a historical export.</p></div>'"
    )[:-1]


def build_driver_doc() -> str:
    items = DRIVER_EMAIL_ITEMS
    body = _cover(
        "Driver Emails — Format & Copy Preview",
        "Every email a driver receives, rendered with the real branded layout and current copy.",
        f"{len(items)} email templates",
    )
    body += "".join(_email_page(i) for i in items)
    return _PAGE_HEAD + body + _PAGE_TAIL


def build_combined_doc() -> str:
    driver_push_sms = [i for i in PUSH_ITEMS + SMS_ITEMS if i["audience"] != "Rider & Driver"]
    shared = [i for i in PUSH_ITEMS + SMS_ITEMS if i["audience"] == "Rider & Driver"]
    total = len(DRIVER_EMAIL_ITEMS) + len(RIDER_EMAIL_ITEMS) + len(PUSH_ITEMS) + len(SMS_ITEMS)
    body = _cover(
        "Rider & Driver Messages — Format & Copy Preview",
        "Every email, SMS, and push notice sent to a rider or a driver, in one document.",
        f"{total} items — {len(DRIVER_EMAIL_ITEMS)} driver email, {len(RIDER_EMAIL_ITEMS)} rider email, "
        f"{len(PUSH_ITEMS)} push, {len(SMS_ITEMS)} SMS",
    )
    body += (
        '<div style="max-width:600px;margin:32px auto 8px;font:700 13px sans-serif;color:#333">— Driver emails —</div>'
    )
    body += "".join(_email_page(i) for i in DRIVER_EMAIL_ITEMS)
    body += (
        '<div style="max-width:600px;margin:32px auto 8px;font:700 13px sans-serif;color:#333">— Rider emails —</div>'
    )
    body += "".join(_email_page(i) for i in RIDER_EMAIL_ITEMS)
    body += '<div style="max-width:600px;margin:32px auto 8px;font:700 13px sans-serif;color:#333">— Push & SMS (shared or per-recipient) —</div>'
    body += "".join(_message_page(i) for i in shared + driver_push_sms)
    return _PAGE_HEAD + body + _PAGE_TAIL


# ── Chromium PDF conversion ──────────────────────────────────────────────────


def _find_chrome() -> str | None:
    env_path = os.environ.get("CHROME_PATH")
    if env_path and Path(env_path).is_file():
        return env_path
    pw_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/opt/pw-browsers")
    for hit in glob.glob(f"{pw_dir}/chromium-*/chrome-linux/chrome"):
        return hit
    for candidate in ("/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/google-chrome"):
        if Path(candidate).is_file():
            return candidate
    return None


def _html_to_pdf(chrome: str, html_path: Path, pdf_path: Path) -> bool:
    result = subprocess.run(
        [
            chrome,
            "--headless",
            "--disable-gpu",
            "--no-sandbox",
            f"--print-to-pdf={pdf_path}",
            "--print-to-pdf-no-header",
            "--no-pdf-header-footer",
            f"file://{html_path}",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return pdf_path.is_file() and result.returncode == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", default=str(REPO_ROOT / "backend" / "scripts" / "_preview_out"))
    args = parser.parse_args()
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    docs = {
        "driver-emails-preview": build_driver_doc(),
        "rider-driver-messages-preview": build_combined_doc(),
    }

    chrome = _find_chrome()
    for name, html_doc in docs.items():
        html_path = out_dir / f"{name}.html"
        html_path.write_text(html_doc, encoding="utf-8")
        print(f"wrote {html_path}")
        if chrome:
            pdf_path = out_dir / f"{name}.pdf"
            if _html_to_pdf(chrome, html_path, pdf_path):
                print(f"wrote {pdf_path}")
            else:
                print(f"PDF conversion failed for {name} (see stderr above)", file=sys.stderr)
        else:
            print(
                "no headless Chromium/Chrome found — HTML written, PDF skipped "
                "(set $CHROME_PATH to a chrome binary to enable PDF output)",
                file=sys.stderr,
            )


if __name__ == "__main__":
    main()
