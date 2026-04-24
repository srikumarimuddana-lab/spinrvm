# Accessibility Plan

**Purpose:** Spinr's commitment to accessible service delivery. Required by the
Accessible Canada Act (ACA) for federally-regulated entities and by provincial
equivalents (where applicable). Also satisfies App Store + Play Store
accessibility requirements and WCAG 2.1 Level AA as the shared bar.

**Owner:** `product` + `legal` · **Cadence:** published at launch, progress
report annually, full re-plan every 3 years.

**Regs:** ACA (federal), AODA (if ON operations), SK-HRC disability
non-discrimination, WCAG 2.1 AA · **D15 dimension**

---

## Applicability

The ACA applies to federally-regulated private-sector entities under the
Canada Labour Code. As a TNC, applicability depends on scope of operations:

- If Spinr is primarily intra-provincial (SK) → provincial human-rights
  frameworks apply directly (SK-HRC) and ACA coverage may be partial
- If Spinr operates cross-provincially or federally → ACA obligations attach
- Conservative default: publish an ACA-style plan to demonstrate
  accessibility commitment regardless of jurisdictional analysis outcome

**Legal review required:** `legal` to file an applicability memo in
`reports/compliance/aca-applicability-memo.md` before public launch.

---

## Principles (per ACA)

Spinr commits to the following accessibility pillars:

1. **Employment** — hiring, retention, accommodation
2. **The built environment** — physical premises (admin offices)
3. **Information and communication technologies (ICT)** — our apps + website
4. **Communication other than ICT** — customer service, written materials
5. **Procurement** — vendor + supplier accessibility requirements
6. **Design and delivery of services** — end-to-end ride experience
7. **Transportation** — vehicle accessibility standards for driver fleet

This document focuses on ICT, services, and transportation — the domains
Spinr most directly controls.

---

## ICT Accessibility (apps + admin)

### Targets
- WCAG 2.1 Level AA conformance for admin-dashboard and any marketing pages
- Mobile accessibility parity (VoiceOver + TalkBack fully supported)

### Rider App
- [ ] All interactive elements have accessible labels (VoiceOver + TalkBack tested)
- [ ] Colour contrast ≥ 4.5:1 for body text, ≥ 3:1 for large text
- [ ] Dynamic type support (font sizes scale with OS setting) up to at least 200%
- [ ] No interaction is colour-only (e.g. surge indication also shown as text)
- [ ] Motion-reduce respected (no mandatory animations blocking interaction)
- [ ] Screen-reader flow covers: address entry → ride confirmation → tracking → payment
- [ ] Captions / non-audio alternatives for any voice/audio content
- [ ] Touch targets ≥ 44×44 pt

### Driver App
- Same general requirements
- [ ] Earnings screens accessible (numeric data read correctly)
- [ ] Safety features (SOS, report-safety) reachable via assistive tech without
  requiring precise tap placement — larger touch targets or gesture shortcut

### Admin Dashboard (Next.js)
- [ ] WCAG 2.1 AA conformance
- [ ] Keyboard navigation for all admin actions (no mouse-only ops)
- [ ] Data tables use proper semantic markup (headers, captions, scope)
- [ ] Forms have label associations and error announcements

### Testing Process
- Automated: `axe-core` + Lighthouse accessibility audit in CI (G11 in
  `docs/ci-security-gates.md` — to be added)
- Manual: VoiceOver + TalkBack testing per release
- User research: include participants with disabilities in beta program

---

## Customer Service Accessibility

- Support accepts inquiries via: in-app chat, email, phone, SMS
- Support agents trained in accessible communication (plain language, avoid
  jargon, confirm understanding)
- Response-time SLA adjusted for relay-service users (e.g. 711 relay) — no
  longer wait than non-relay users
- Alternative formats: Spinr will provide documentation in Braille, large
  print, or ASL-video on request at no charge

---

## Transportation / Fleet Accessibility

**Near-term:** Spinr's driver fleet is not directly owned; accessibility of
vehicles depends on individual driver vehicles.

**Current commitments:**
- Riders can filter for wheelchair-accessible vehicles in future release
  (product roadmap)
- Service animal accommodation: drivers may NOT refuse service to riders
  with service animals (SK-HRC) — addressed in driver terms of service and
  onboarding education
- Pickup / drop-off flexibility: riders may specify accessibility notes in
  ride requests

**Long-term:**
- [ ] WAV (wheelchair-accessible vehicle) partnership program
- [ ] Priority dispatch for users who have registered accessibility needs
- [ ] Fare-equality rule: WAV rides not surge-priced differently

---

## Employment

- Spinr commits to accommodation for hiring, onboarding, and ongoing employment
- Recruiting materials include accessibility statement
- Interview process includes reasonable-accommodation intake

---

## Procurement

New vendor onboarding (per `docs/vendor-inventory.md` checklist) must include:
- [ ] Does the vendor meet WCAG 2.1 AA (if ICT)?
- [ ] Does the vendor have its own accessibility policy?
- [ ] Can the vendor provide accommodations Spinr's users may require?

---

## Feedback Mechanism

Per ACA, Spinr must offer an accessible feedback channel. Options:

- Email: `accessibility@spinr.ca` (to be provisioned)
- Phone: relay-service-friendly support line
- In-app: "Feedback on accessibility" entry point in settings
- Mail: physical address with large-print option

Feedback is logged and reviewed by `product` quarterly. Actionable items
flow into `OPEN-ITEMS-TRACKER.md`.

---

## Reporting

Per ACA (for federally-regulated entities):

| Artifact | Due | Owner |
|---|---|---|
| Initial accessibility plan | At launch (or legal-memo-defined date) | product + legal |
| Progress report #1 | 1 year after plan publication | product |
| Progress report #2 | 2 years after plan publication | product |
| Updated plan | 3 years after initial plan | product + legal |
| Feedback summary | Annually with progress report | product |

Publication location: spinr.ca/accessibility (to be set up) + this repo under
`reports/accessibility/YYYY-progress.md`.

---

## Known Gaps (at 2026-04-24)

| Gap | Severity | Owner | Target |
|---|---|---|---|
| ACA applicability memo not filed | HIGH | legal | Pre-launch |
| Accessibility feedback channels not live | HIGH | product | Pre-launch |
| Axe-core / Lighthouse not in CI | MEDIUM | admin + devops | P2 sprint |
| WAV / service animal policy not in driver onboarding | HIGH | product | Pre-launch |
| Annual progress-report process not set up | LOW | product | After launch |

---

## Change Log

| Date | Change | Author |
|---|---|---|
| 2026-04-24 | Initial plan skeleton | audit-framework |
