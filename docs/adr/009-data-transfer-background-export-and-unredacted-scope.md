# ADR-009: Data Transfer export runs as a background job, and is deliberately unredacted

**Date:** 2026-07-28
**Status:** Accepted (decisions made during the module's original build,
Phase 1.2–1.3, commits `a5468cc`/`4978da8`; documented here retroactively
per the module's lifecycle audit, gap H6)

---

## Context

The Data Transfer module's export endpoint
(`routes/admin/data_transfer_export.py`) bundles one or more users/drivers
(profile, documents, ride history, insurance-period audit trail) into a
downloadable ZIP so an admin can move the same records into another Spinr
environment the company operates. Two decisions made during its original
build are the kind a future engineer working on this module — or a
similar one — could plausibly get backwards by default, and neither was
captured as an ADR:

1. **Should the export happen synchronously (request → response with the
   file), or asynchronously (accept the request, do the work in the
   background, let the caller poll for status)?**
2. **Should this export redact PII the way the existing DSAR self-export
   (`routes/drivers/tax_exports.py`) does for a user downloading their own
   data?**

## Decision 1: Background job, not request/response

**Chosen:** the export route validates the request, inserts a `pending`
row into `data_transfer_export_jobs` (migration 262), and returns
`202 Accepted` with a `job_id` immediately. The actual gather → build →
upload work runs via `BackgroundTasks.add_task` after the response is
sent. The admin polls `GET /data-transfer/jobs/{job_id}` (or watches the
Jobs & History tab) to discover completion and get the download link.

**Rejected: synchronous request/response.** The module's first
implementation worked this way — gather, zip, upload, then return the
file — and was replaced (`4978da8 perf(admin): background the Data
Transfer export route`) specifically because of a concrete risk: the
route accepts up to `MAX_ENTITIES_PER_EXPORT = 100` entities per call,
each potentially carrying multiple documents' worth of file bytes to
fetch, zip, and upload. Tying up a request/response cycle for that
duration risked timeouts (both at the FastAPI/Uvicorn layer and any
upstream proxy/load-balancer timeout) on a legitimately large but valid
batch — a real operational failure mode, not a hypothetical one.

**Why this is the right trade-off, not just a workaround:** the
alternative to backgrounding isn't "make it faster" — bundling document
bytes for 100 entities is inherently I/O-bound work with no shortcut.
Backgrounding trades a worse property (a hung/timed-out HTTP request,
with no way to know if the export actually completed) for a better one
(the admin gets an immediate acknowledgment and a durable, pollable
status they can leave and return to) at the cost of a slightly more
complex UX (poll instead of instant download). For an admin-only,
low-frequency operation, that trade is clearly correct — it would not
necessarily be correct for a rider/driver-facing hot path with a P95
SLA target (see CLAUDE.md's Performance SLA table), which is why this
pattern isn't proposed as a default for other admin exports without the
same volume justification.

## Decision 2: Unredacted, full-fidelity export — deliberately, not by omission

**Chosen:** `services/data_transfer/entity_export_service.py` gathers the
*complete* record for each requested entity — profile fields, decrypted
driver PII (via the same `_vault_encrypt`/decrypt path used elsewhere),
document file bytes (not just metadata), ride history, and insurance-period
audit trail — with no redaction applied. The module's own docstring
states this explicitly: *"Unlike the PIPEDA self-export in
`routes/drivers/tax_exports.py` (which redacts fields for the account
holder's own download), this gathers data for an admin moving a record
between Spinr's own environments — no redaction, because the operator
already has full admin visibility into the source data."*

**Rejected: reusing the DSAR self-export's redaction/field-list logic.**
The two export paths look superficially similar (both produce a
downloadable bundle of a user's data) and reusing one code path might
seem like the DRY choice. It is the wrong one: the DSAR export's
redaction rules exist to protect the account holder's own view of
*their* data from including things they shouldn't have visibility into
(or that would be operationally sensitive to hand back verbatim) — that
threat model does not apply here, since the caller is an admin who
already has full visibility into the source data via the admin panel
itself. Redacting this export wouldn't add security; it would produce a
recipient environment with silently incomplete records — a worse outcome
than the DRY convenience would justify. Sharing the code path would also
make it easy for a future change to the DSAR redaction rules (tightened
for a real privacy reason) to silently degrade this operator-to-operator
transfer, or vice versa — a future loosening of this export's field list
"for convenience" to silently leak into the self-export path.

**Why this decision needs its own gate instead:** because this export is
correctly unredacted, it is also the single highest-blast-radius
endpoint in the admin panel (see the threat-model cross-reference in
`docs/threat-model/admin-panel.md`'s `AI-3` finding and this module's
lifecycle audit, gap H2) — the mitigation for that risk is rate limiting
(already shipped, `data_transfer_export_limit`, 10/hour) and, eventually,
AI-3's dual-approval mechanism, not redaction. Redacting this export to
reduce its blast radius would defeat its actual purpose (a full-fidelity
environment migration tool) while providing weaker protection than a
proper approval gate would.

## Consequences

**Positive:**
- The background-job pattern is now documented as the module's
  established shape for any future "accepts a large batch, does I/O-bound
  work" admin operation — a future contributor extending this module (or
  building a similar one) has a citable precedent instead of needing to
  rediscover the timeout risk themselves.
- The redaction decision is now discoverable independent of reading
  `entity_export_service.py`'s docstring — a future engineer asking "can
  I just reuse the DSAR export logic here" (or the reverse) has an
  explicit, reasoned answer to find.

**Negative / trade-offs:**
- The background-job pattern means there is no synchronous "did it work"
  signal — a caller (UI or script) must implement polling, and the
  original synchronous version's simpler contract is gone. Accepted,
  since the alternative (request timeouts on legitimate large batches)
  is worse.
- The unredacted-by-design decision means this endpoint's correctness
  depends entirely on its *access* controls (module grant, rate limit,
  eventually AI-3's dual-approval) rather than any data-minimization
  property of the export itself. If those access controls are ever
  weakened, there is no redaction fallback protecting the data — this is
  a deliberate, not accidental, single point of enforcement, and should
  be kept in mind before any future change to `require_module`,
  `data_transfer_export_limit`, or AI-3's eventual gate.
