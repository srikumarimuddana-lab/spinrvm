# Email Client Rendering Verification — Options & Recommendation

**Status:** planning document, not a runbook. Written to inform the vendor/
tooling decision ACTION_ITEMS.md's N12 is still open pending — this doc
does not make that decision or close N12. Format follows
`docs/finance/stripe-payout-readiness.md` (risk framing, options with real
tradeoffs, a recommended lightweight first step, explicit "what this does
NOT resolve"). No code changes accompany this document.

## 1. What N12 is actually asking, and what's already closed

ACTION_ITEMS.md N12 originally read "no visual/snapshot regression tooling
for email." As of 2026-08-12 that is **half true**: golden-file snapshot
tests (`backend/tests/test_email_snapshots.py`, using the diff helper in
`backend/tests/_html_snapshot.py`) now pin the exact HTML and plain-text
bytes that `utils/email_layout.py`'s `render_email`/`render_from_text` and
`utils/email_receipt.py`'s legacy/branded shells generate. That closes
**source drift** — a refactor that silently drops a `role="presentation"`,
un-closes a `<tr>`, or strips a style attribute now fails a test with a
readable diff, before it ships.

What it does **not** close, and what this document is about: nothing in
this repo confirms that the HTML those snapshots pin actually *displays
correctly* once a real client's rendering engine gets hold of it. A
snapshot test can stay green forever while Outlook silently ignores a CSS
property, mangles a media query, or renders a `<div>` fallback as unstyled
text — the snapshot only proves "this is still what we generate," not
"this is still what a recipient sees."

## 2. Risk assessment specific to these templates (not generic email advice)

Read `backend/utils/email_layout.py` (the shared shell every transactional
email renders through), `backend/utils/email_receipt.py` (the ride receipt,
legacy and branded shells), `backend/utils/rider_emails.py`, and
`backend/utils/driver_status_notifications.py` to ground this in what the
repo actually emits, not a generic client-support checklist.

**Good news first — the layout is already built defensively:**

- **Table-based layout throughout**, not CSS Grid or Flexbox. `render_email`
  nests `<table role="presentation">` elements for the header band, body,
  and footer (`email_layout.py:416-426`); `email_receipt.py` does the same
  for the receipt card. This is the correct baseline choice — Outlook's
  Word rendering engine has no support for Grid/Flexbox at all, and this
  repo never reaches for either, so that entire failure class doesn't
  apply here.
- **Inline styles on every element**, not a `<style>` block driving layout.
  The one `<style type="text/css">` block that exists
  (`email_layout.py:_HEAD_STYLE`, lines 337-350) is scoped to two things
  Outlook desktop doesn't need to honor for the layout to still work: a
  `max-width:620px` responsive padding tweak and a
  `prefers-color-scheme: dark` override. If Outlook ignores that block
  entirely (it does — Word doesn't parse `<style>` in `<head>` reliably),
  the email still renders correctly at full width in light mode; nothing
  structural depends on it.
- **`<br>` instead of `white-space:pre-line`** for multi-line content
  (`_esc_multiline`, `email_layout.py:113-135`) — the module's own comment
  explains this was a deliberate fix for Outlook's inconsistent handling of
  that CSS property, applied to real content that depends on preserved line
  breaks (the safety-team incident alert, driver statement totals).
- **No webfonts.** `FONT_STACK` (`email_layout.py:87`) leads with a system
  font stack rather than `@font-face`-loading Plus Jakarta Sans, because
  "Outlook desktop ignores `@font-face` entirely" — again, degrades by
  design rather than by omission.
- **Remote-image alt text styled to stand in for the logo**
  (`header_html`, `email_layout.py:180-184`) for clients that block remote
  images by default (Outlook, and Gmail before the sender is trusted).

**Real, live risk surfaces — the specific things worth verifying, not a
generic checklist:**

1. **`border-radius` and `box-shadow` in `email_receipt.py`** (lines 424,
   469, 490, 493, 495, 516, 518) — used on the receipt card, the map-image
   corners, and small colored dots/avatar circles. Outlook desktop's Word
   engine does not render `border-radius` (renders square) or `box-shadow`
   (ignored). This is a cosmetic degradation, not a broken layout — worth
   confirming it actually looks acceptable square-cornered, not assuming
   it does.
2. **The route/map snapshot image** (`email_receipt.py`, `include_route_snapshot`)
   — a remote image, blocked by default in Outlook and untrusted Gmail
   senders. The receipt is expected to be legible with images off; nothing
   in the current tests confirms that visually.
3. **`prefers-color-scheme: dark` override** (`_HEAD_STYLE`,
   `email_layout.py:345-349`) — support for this media query inside an
   email `<style>` block is inconsistent across clients (notably varies by
   Apple Mail version and is not supported by Outlook or by Gmail's own
   dark-mode re-coloring, which instead auto-inverts colors on its own
   heuristic and can clash with an explicit override). The module
   deliberately keeps the header band light-only in dark mode (comment at
   `email_layout.py:330-336`) specifically because a wrong dark-mode
   background would erase the charcoal logo — that reasoning is sound, but
   whether Gmail's auto-dark-mode heuristic actually respects it, rather
   than reinventing its own colors on top, is unverified.
4. **`&nbsp;`-padded alignment** (`_esc_multiline`, `email_layout.py:134-135`)
   for column-aligned plain content (incident alert fields, statement
   totals) — relies on the recipient's font being reasonably monospaced-ish
   in spacing; not something a snapshot test can catch, since the bytes are
   correct even if the visual alignment looks poor in a proportional font.
5. **Preheader hiding technique** (`_preheader_html`, `email_layout.py:319-327`)
   — `display:none` + `mso-hide:all` + `max-height:0` is the standard
   three-part defense against clients that partially support one hiding
   method but not another; this is good practice but its actual
   invisibility (not just "not obviously broken") has never been confirmed
   against real inboxes for this repo's specific rendering.

**Explicitly out of scope for this pass:** `backend/utils/subscription_invoice.py`
generates a PDF (not raw HTML) and is DB-dependent — different shape of
problem, not covered by the golden-file snapshots and not covered by this
document either.

**Net assessment:** the templates are built by someone who already knew
Outlook's failure modes and designed around them defensively. The residual
risk is narrow — a handful of specific cosmetic degradations (items 1-4
above) rather than a structurally broken layout — which argues for a
lightweight verification step over an expensive continuous pipeline. That
conclusion drives the recommendation in §4.

## 3. Options for real per-client rendering verification

### Option A — Paid rendering-verification service (Litmus / Email on Acid)

Send a rendered email through the service's test-send address; it returns
screenshots (or a live interactive preview) across dozens of real
client/OS/viewport combinations (Outlook 2016/2019/365 desktop, Outlook.com
web, Gmail web/iOS/Android, Apple Mail macOS/iOS, and others), usually
within a few minutes.

- **Cost:** subscription-based, typically in the low-to-mid hundreds of
  USD/month depending on tier and send volume (both vendors publish tiered
  plans; exact current pricing should be re-checked at decision time, not
  assumed from this doc — pricing pages change). Litmus and Email on Acid
  are the two dominant vendors; Mailtrap also ships an email-testing
  product bundled with its broader email-sandbox tooling, generally
  positioned as the cheaper of the three but with a smaller client matrix.
- **Setup effort:** low. Both integrate via a test-send SMTP address or API
  call — could be wired into a CI step or a pre-release manual trigger with
  a few hours of work once a plan is purchased.
- **Ongoing maintenance:** low once wired up; the main cost is the
  recurring subscription itself, plus periodically re-running the full
  client matrix after any template change (not just trusting it stays
  correct because the snapshot test is green).
- **Coverage:** the real thing — actual client rendering engines, not an
  approximation. This is the only option that would have caught an actual
  Outlook `border-radius` degradation with a screenshot rather than
  someone's judgment call.
- **Requires:** a new vendor contract and someone to own the relationship
  (billing, plan tier, who gets login access). Not something this session
  can set up — no API keys or accounts are available here, and starting a
  paid contract is a decision for whoever owns the finance/vendor
  relationship, per the CLAUDE.md working style on cost-incurring
  decisions.

### Option B — Manual test-account matrix (free, real accounts)

Maintain a small set of real, free test accounts — a Gmail account, an
Outlook.com account, and access to Apple Mail (any Apple ID on a real
device or the macOS/iOS simulator) — and manually send/forward a rendered
email to all three before merging a template change, eyeballing the
result against the risk list in §2.

- **Cost:** $0. Three free email accounts plus whatever device/simulator
  access already exists for driver-app/rider-app iOS testing (Apple Mail
  needs a real Apple device or simulator, which the mobile team likely
  already has for other reasons).
- **Setup effort:** low — create three accounts once, document the
  checklist (see §4).
- **Ongoing maintenance:** real but bounded — a person has to actually run
  the check before each template change, which is a discipline problem,
  not a tooling problem. No automation, no CI enforcement; it depends on
  someone remembering to do it, the same way `admin-dashboard`'s
  zero-baseline visual-regression job (ACTION_ITEMS.md B38, referenced in
  CLAUDE.md's release-gate rule 6) depends on someone seeding baselines —
  a known failure mode for "informal process" checks in this repo.
- **Coverage:** only whichever three inboxes are actually checked — no
  Outlook desktop (Word engine) coverage unless someone also has a Windows
  machine with real Outlook installed, which is the single highest-risk
  client for this codebase's `border-radius`/`box-shadow` usage (§2, item
  1) and is not covered by Outlook.com web mail. This is a real coverage
  gap in this option, not a minor one — Outlook.com's web client uses a
  different, more modern rendering path than Outlook desktop's Word
  engine, so testing only Outlook.com does not validate the desktop case
  that actually motivated `_esc_multiline`'s `<br>` choice.

### Option C — `Can I Email` compatibility-table cross-referencing (free, static)

For each CSS feature these templates actually use (§2's list — table
layout, inline styles, `border-radius`, `box-shadow`, `prefers-color-scheme`
in a `<style>` block, `mso-hide`), look up current support in the
community-maintained "Can I Email" compatibility tables
(caniemail.com — the email-client equivalent of caniuse.com, aggregating
tested support across Outlook/Gmail/Apple Mail/Yahoo/etc. per CSS
property) and record what's expected to degrade and how.

- **Cost:** $0.
- **Setup effort:** low — a single research pass, already substantially
  done in §2 of this document.
- **Ongoing maintenance:** re-check only when a template adds a CSS
  property not already covered (e.g. if a future template introduces
  `flexbox` or CSS `gap`, check it before shipping).
- **Coverage:** tells you *what should happen in theory*, based on other
  people's aggregated test results — not a screenshot of *this specific
  email* rendering in *this specific client version*. Useful as a fast
  pre-check to catch an obviously bad idea (e.g. reaching for Flexbox)
  before it ships, but it does not replace an actual look at rendered
  output, because real client bugs and version-specific quirks
  (interaction effects between properties, not just single-property
  support) don't show up in a per-property table.
- **Note on MJML:** the task brief raised MJML (a framework that compiles
  a simplified markup language down to battle-tested table-based email
  HTML) as a possible option. It is **not recommended here**: these
  templates already hand-author the exact defensive table structure MJML
  would generate for them (see §2's "good news" list) — porting to MJML
  would mean rewriting `email_layout.py`'s ~500 lines and
  `email_receipt.py`'s ~750 lines to buy back structural safety this repo
  already has by hand, at real risk of regressing the snapshot tests and
  the careful Outlook-specific workarounds already in place
  (`_esc_multiline`'s `<br>` choice, the alt-text-as-wordmark fallback).
  Not worth it unless a future template needs layout complexity (e.g. a
  responsive multi-column driver statement) that outgrows hand-authored
  tables — flag as a "maybe later," not a near-term recommendation.

## 4. Recommendation

**Immediate, no-cost step: adopt Option B as a documented pre-merge
checklist, informed by Option C's findings in §2, as the interim measure
pending a vendor decision on Option A.**

This is the same posture CLAUDE.md's release-gate rule 6 already takes for
admin-dashboard's un-seeded visual-regression baselines: name the gap
explicitly rather than letting "no visible diff in code review" stand in
for verification.

### Checklist: manual email-client verification before a template change

Run this before merging any change to `backend/utils/email_layout.py`,
`backend/utils/email_receipt.py`, or any template that changes the shared
shell's structure (not required for a pure copy/text change that doesn't
touch markup or styles):

1. Render the email locally (existing snapshot tests already do this —
   pull the rendered HTML output, or trigger a real send via the dev
   environment's transactional-email path).
2. Send it to three real test-account addresses:
   - A Gmail address (web + mobile app if available)
   - An Outlook.com address, **and**, if any team member has access to
     desktop Outlook (Windows, real Word-engine rendering) — that is the
     highest-risk client for this codebase's `border-radius`/`box-shadow`
     usage (§2, item 1) — check there too. Note explicitly in the PR
     description whether desktop Outlook was actually checked or only
     Outlook.com web, since they are materially different engines.
   - An Apple Mail address (macOS or iOS, real device or simulator)
3. With images loading normally, confirm: logo displays, receipt card
   corners/shadow degrade acceptably (square is fine; broken layout is
   not), dark mode (if the client supports it) doesn't erase the header
   band or logo.
4. With remote images blocked (most clients have a "don't load images"
   toggle, or default to it for new senders) confirm: the alt-text wordmark
   is legible, the email is still usable without the route/map image.
5. Confirm the preheader text (the inbox preview line) shows the intended
   summary, not raw markup or the logo alt text.
6. Note any client-specific degradation in the PR description, even a
   cosmetic one — this is the record that would otherwise only exist in
   someone's memory.

This costs no new vendor relationship, uses accounts that likely already
exist for other testing purposes, and directly targets the five specific
risk items in §2 rather than a generic client sweep.

### When to revisit Option A

If email volume or template complexity grows enough that manual checking
becomes a real bottleneck (frequent template changes, multiple people
authoring templates, or a regression that Option B's checklist actually
missed and shipped), that is the trigger to bring a Litmus/Email on
Acid/Mailtrap proposal to whoever owns vendor spend — with this document's
§2 risk list as the concrete justification, not a generic "we should have
this" pitch.

## 5. What this document does NOT resolve

- Does not select a vendor or commit budget for Option A — that is a
  decision for whoever owns vendor spend, same posture as
  `docs/finance/stripe-payout-readiness.md` §5 on the Stripe conversation.
- Does not build any pipeline, script, or CI job — this session has no
  paid rendering-verification service access (no API keys/accounts) and no
  ask from the user to build the manual-checklist automation.
- Does not verify current Litmus/Email on Acid/Mailtrap pricing or plan
  details — re-check at decision time; pricing pages change and this
  document should not be treated as a live price quote.
- Does not close ACTION_ITEMS.md N12 — see that entry for the current
  status; this document informs the still-open vendor/tooling decision, it
  doesn't make it.
