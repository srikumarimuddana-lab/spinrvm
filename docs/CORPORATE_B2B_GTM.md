# Corporate/B2B Go-To-Market — Sales & Positioning Strategy

**Status:** strategy reference, not architecture. This is a sibling to
`docs/CORPORATE_B2B.md`, which is the system-map/data-model/correctness
source of truth for how corporate accounts actually work in code — **if
this document and that one ever disagree on how the product behaves, trust
`CORPORATE_B2B.md`.** This document covers who to sell to and how, which
`CORPORATE_B2B.md` deliberately does not.

Owner: sales/growth lead. Nothing here is code-verified or Spinr sales
data — there is no Spinr corporate-account sales history yet.

## 1. Strongest single finding: Captain Taxi already sells this in Saskatoon

Captain Taxi (an existing Saskatoon taxi operator) already sells a
monthly-invoiced corporate account product to "businesses, clinics, and
government offices" today. This is the single strongest piece of evidence
in this whole GTM plan: it's direct proof of displaceable local demand,
not a hypothetical market — a Saskatoon buyer already pays a competitor for
roughly this product. Spinr's pitch to that same buyer is a real
alternative, not an unproven category.

## 2. Named early targets (from research, not confirmed conversations)

- **Resident Doctors of Saskatchewan** — has an existing $25/occurrence
  taxi-fare reimbursement policy. This is evidence of an *existing budget
  line* for exactly this use case — a corporate account pitch here is
  replacing an existing informal reimbursement process with a managed one,
  which is usually an easier sale than creating new budget.
- **Saskatchewan Health Authority (SHA)** — nurse safety response (getting
  staff home safely after late/overnight shifts) is a live, publicly
  discussed pain point. Likely a longer, more formal sales cycle (large
  institutional buyer) than the clinic-level targets below — see the
  phased plan in §4.
- **Clinics and small government offices** — the segment Captain Taxi is
  already serving per §1. Lower-friction self-serve targets to start with.

None of the above are confirmed conversations or signed pipeline — they are
research-identified candidates. Sales should validate each independently
before building pitch collateral specific to them.

## 3. Pricing/product pattern reference (industry, not Spinr commitments)

Uber for Business, Lyft Business, and DoorDash for Business all use a
two-tier pattern: a self-serve tier (credit card on file, no sales contact,
usage-based or flat monthly) for small accounts, and a sales-assisted
enterprise tier (invoiced, negotiated terms, dedicated support) for large
institutional accounts. Spinr's existing `CORPORATE_B2B.md` architecture
(wallet/allowance model, `corporate_wallet_apply_delta` for row-locked
deltas) already supports a self-serve-style flow — the GTM decision is
which tier to lead with per target, not a build decision.

## 4. Phased 90-day plan (draft)

| Phase | Target | Motion | Notes |
|---|---|---|---|
| Days 0-30 | Clinics / small government offices (§2, §1) | Self-serve floor — sign up, load a wallet, start booking | Lowest-friction segment; validates the product with real usage before a harder institutional sale |
| Days 30-60 | Resident Doctors of Saskatchewan-style targets (existing informal reimbursement budget) | Light-touch sales-assisted — replace an existing process, not create a new budget ask | Use the existing $25/occurrence reimbursement figure as an anchor in the pitch, not a Spinr price commitment |
| Days 60-90 | SHA / university-scale institutional targets | Full sales-assisted, procurement-cycle-aware | Expect a materially longer cycle than the self-serve segment; do not treat Days 0-30 momentum as predictive of this phase's timeline |

## 5. What this GTM plan depends on from the product side

- Corporate billing correctness (idempotent wallet deltas, allowance caps)
  is already covered by `CORPORATE_B2B.md` and enforced by the
  `spinr-corporate-billing-reviewer` agent — this GTM plan assumes that
  invariant holds and does not re-litigate it.
- Corporate reporting/export (what an admin at a client company sees) is
  covered by the reporting surface audited by
  `spinr-corporate-reporting-reviewer` — a self-serve pitch to a clinic
  office manager leans heavily on this being clear and correct, since
  there's no dedicated account manager smoothing over a confusing invoice.
- Surge does not apply to corporate account-paid rides (CLAUDE.md policy,
  verify in fare service) — this is a genuine differentiator worth stating
  explicitly in pitch collateral: a corporate buyer gets predictable
  per-ride cost, unlike a consumer card on file during a surge window.

## 6. What this document does NOT cover

- Corporate billing/wallet correctness — `docs/CORPORATE_B2B.md`
- Corporate account/membership/policy lifecycle — `.claude/context/domain-corporate.md`
- Consumer rider/driver acquisition — `docs/growth/`

## Sources (external research, not Spinr-verified)

Captain Taxi Saskatoon corporate-account product (public web presence, not
independently confirmed via a direct sales conversation); Resident Doctors
of Saskatchewan taxi-fare reimbursement policy (public reporting); SHA
nurse-safety discussion (public reporting); Uber for Business / Lyft
Business / DoorDash for Business self-serve-vs-enterprise pricing pattern
(public product pages). None of this is Spinr sales data.
