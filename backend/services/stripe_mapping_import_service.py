"""Legacy Stripe mapping import — maps old-app Stripe IDs onto new rows.

The legacy Spinr app's drivers and riders were already imported
(``driver_import_service.py``); this service carries over their **Stripe
identities** so drivers keep their Connect Express account (bank details,
identity verification) and riders keep their saved cards:

- kind="drivers": CSV of ``old_driver_id``/``phone`` → ``stripe_account_id``
  written to ``drivers.stripe_account_id``.
- kind="riders": CSV of ``phone``/``email`` → ``stripe_customer_id``
  written to ``users.stripe_customer_id``.

The CSV's Stripe IDs must be valid on THIS platform's key: either the old and
new apps share one Stripe account, or the IDs come from Stripe's official
account-to-account / platform-to-platform migration mapping file (see
docs/runbooks/stripe-legacy-migration.md). Every surviving row is validated
live against Stripe before commit; a wrong-scenario CSV fails loudly with
``not_accessible`` on every row.

Plan/commit shape mirrors ``driver_import_service``: ``build_plan`` never
writes; ``commit_plan`` refuses while errors exist; a re-run converges because
already-mapped rows are skipped with a warning and commit only ever fills NULL
columns. Reports carry only ``row_ref`` (old_driver_id / old_user_id / CSV line
number) — never phones, emails, or names — per the PIPEDA rules in CLAUDE.md.
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

try:
    from ..supabase_client import supabase
    from .driver_import_service import _select_in, normalize_phone, read_csv_text
except ImportError:  # pragma: no cover - allow direct/CLI module imports
    from services.driver_import_service import _select_in, normalize_phone, read_csv_text
    from supabase_client import supabase  # type: ignore  # noqa: F401

logger = logging.getLogger(__name__)

IMPORT_SOURCE = "legacy_stripe_migration"
# Must match driver_import_service.IMPORT_SOURCE — old_driver_id lookups key on
# the provenance that importer stamped.
DRIVER_IMPORT_SOURCE = "legacy_saskatoon_driver_import"

KIND_DRIVERS = "drivers"
KIND_RIDERS = "riders"
VALID_KINDS = {KIND_DRIVERS, KIND_RIDERS}

# Stay far below Stripe's rate limit; the admin commit path re-validates
# inline, so a full batch is bounded by MAX_ROWS in the route.
MAX_STRIPE_CONCURRENCY = 8

ACCT_RE = re.compile(r"^acct_[A-Za-z0-9]+$")
CUS_RE = re.compile(r"^cus_[A-Za-z0-9]+$")


@dataclass
class StripeMappingErrorItem:
    row_ref: str  # old_driver_id / old_user_id when present, else "row-<n>" — never raw PII
    field: str
    message: str


@dataclass
class StripeMappingPlan:
    kind: str
    batch: str
    driver_updates: list[dict[str, Any]] = field(default_factory=list)
    #   {"driver_id", "stripe_account_id", "row_ref", "old_stripe_account_id",
    #    "existing_metadata"}
    user_updates: list[dict[str, Any]] = field(default_factory=list)
    #   {"user_id", "stripe_customer_id", "row_ref", "old_stripe_customer_id",
    #    "old_user_id", "existing_metadata"}
    warnings: list[StripeMappingErrorItem] = field(default_factory=list)
    errors: list[StripeMappingErrorItem] = field(default_factory=list)
    needs_update: list[dict[str, Any]] = field(default_factory=list)
    #   drivers already carrying a DIFFERENT stripe_account_id — NON-blocking.
    #   Never auto-written by commit_plan; surfaced for an explicit, confirmed
    #   per-driver update via update_driver_stripe_account. Items:
    #   {"row_ref", "driver_id", "current_stripe_account_id", "new_stripe_account_id"}


def parse_mapping_rows(text: str, kind: str) -> list[dict[str, str]]:
    """Parse the mapping CSV and enforce the per-kind required columns."""
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown kind {kind!r}; expected one of {sorted(VALID_KINDS)}")
    rows = read_csv_text(text)
    if not rows:
        raise ValueError("CSV has no data rows")
    headers = set(rows[0].keys())
    if kind == KIND_DRIVERS:
        if "stripe_account_id" not in headers:
            raise ValueError("drivers mapping CSV requires a stripe_account_id column")
        if not headers & {"old_driver_id", "phone"}:
            raise ValueError("drivers mapping CSV requires an old_driver_id or phone column")
    else:
        if "stripe_customer_id" not in headers:
            raise ValueError("riders mapping CSV requires a stripe_customer_id column")
        if not headers & {"phone", "email"}:
            raise ValueError("riders mapping CSV requires a phone or email column")
    return rows


def _row_ref(row: dict[str, str], idx: int, key: str) -> str:
    """Stable, PII-free row reference: the legacy ID when present, else the
    1-based CSV line number (header is line 1)."""
    legacy = (row.get(key) or "").strip()
    return legacy if legacy else f"row-{idx + 2}"


def _prefetch_drivers(
    rows: list[dict[str, str]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Batched lookups for the drivers kind.

    Returns (by_old_id, by_phone, by_stripe_account_id). old_driver_id match
    requires legacy-import metadata (source check). Phone match is unscoped
    (any driver) — safety comes from the NULL-guard on commit + super_admin
    gate + live Stripe validation. by_acct is deliberately unscoped: it
    detects value collisions across the whole table.
    """
    old_ids = sorted({(r.get("old_driver_id") or "").strip() for r in rows} - {""})
    phones = sorted({normalize_phone(r.get("phone") or "") for r in rows if (r.get("phone") or "").strip()})
    accts = sorted({(r.get("stripe_account_id") or "").strip() for r in rows} - {""})

    cols = "id,phone,stripe_account_id,legacy_import_metadata"
    by_old_id: dict[str, dict[str, Any]] = {}
    by_phone: dict[str, dict[str, Any]] = {}
    by_acct: dict[str, dict[str, Any]] = {}

    if old_ids:
        for d in _select_in("drivers", cols, "legacy_import_metadata->>old_driver_id", old_ids):
            meta = d.get("legacy_import_metadata") or {}
            if meta.get("source") != DRIVER_IMPORT_SOURCE:
                continue
            key = str(meta.get("old_driver_id") or "")
            if key and key not in by_old_id:
                by_old_id[key] = d
    if phones:
        for d in _select_in("drivers", cols, "phone", phones):
            key = d.get("phone")
            if key is not None and key not in by_phone:
                by_phone[key] = d
    if accts:
        for d in _select_in("drivers", "id,stripe_account_id", "stripe_account_id", accts):
            key = d.get("stripe_account_id")
            if key and key not in by_acct:
                by_acct[key] = d
    return by_old_id, by_phone, by_acct


def _prefetch_users(
    rows: list[dict[str, str]],
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Batched lookups for the riders kind: (by_phone, by_email, by_customer_id)."""
    phones = sorted({normalize_phone(r.get("phone") or "") for r in rows if (r.get("phone") or "").strip()})
    emails = sorted({(r.get("email") or "").strip().lower() for r in rows if (r.get("email") or "").strip()})
    customers = sorted({(r.get("stripe_customer_id") or "").strip() for r in rows} - {""})

    cols = "id,phone,email,stripe_customer_id,legacy_import_metadata"
    by_phone: dict[str, dict[str, Any]] = {}
    by_email: dict[str, dict[str, Any]] = {}
    by_customer: dict[str, dict[str, Any]] = {}

    if phones:
        for u in _select_in("users", cols, "phone", phones):
            key = u.get("phone")
            if key is not None and key not in by_phone:
                by_phone[key] = u
    if emails:
        for u in _select_in("users", cols, "email", emails):
            key = (u.get("email") or "").strip().lower()
            if key and key not in by_email:
                by_email[key] = u
    if customers:
        for u in _select_in("users", "id,stripe_customer_id", "stripe_customer_id", customers):
            key = u.get("stripe_customer_id")
            if key and key not in by_customer:
                by_customer[key] = u
    return by_phone, by_email, by_customer


def _check_duplicate_stripe_ids(
    candidates: list[tuple[int, dict[str, str], str, str]],
    plan: StripeMappingPlan,
) -> list[tuple[int, dict[str, str], str, str]]:
    """Drop rows whose Stripe ID appears more than once in the CSV (error on each)."""
    counts = Counter(stripe_id for _, _, _, stripe_id in candidates)
    kept: list[tuple[int, dict[str, str], str, str]] = []
    for idx, row, row_ref, stripe_id in candidates:
        if counts[stripe_id] > 1:
            plan.errors.append(
                StripeMappingErrorItem(row_ref, "duplicate_in_csv", f"{stripe_id} appears on multiple CSV rows")
            )
        else:
            kept.append((idx, row, row_ref, stripe_id))
    return kept


def _drop_duplicate_targets(
    plan: StripeMappingPlan, updates: list[dict[str, Any]], target_key: str
) -> list[dict[str, Any]]:
    """Drop updates whose target row is hit by more than one CSV row (error on each)."""
    counts = Counter(u[target_key] for u in updates)
    kept: list[dict[str, Any]] = []
    for u in updates:
        if counts[u[target_key]] > 1:
            plan.errors.append(
                StripeMappingErrorItem(u["row_ref"], "duplicate_target", "multiple CSV rows resolve to the same record")
            )
        else:
            kept.append(u)
    return kept


def _build_local_plan(kind: str, rows: list[dict[str, str]], batch: str) -> StripeMappingPlan:
    """Phase 1: matching + local guards. No Stripe calls, no writes.

    Populates plan.driver_updates / plan.user_updates with candidates that
    still need live Stripe validation (build_plan phase 2).
    """
    plan = StripeMappingPlan(kind=kind, batch=batch)
    if kind == KIND_DRIVERS:
        _build_local_driver_plan(rows, plan)
    else:
        _build_local_rider_plan(rows, plan)
    return plan


def _build_local_driver_plan(rows: list[dict[str, str]], plan: StripeMappingPlan) -> None:
    candidates: list[tuple[int, dict[str, str], str, str]] = []
    for idx, row in enumerate(rows):
        row_ref = _row_ref(row, idx, "old_driver_id")
        acct = (row.get("stripe_account_id") or "").strip()
        if not ACCT_RE.match(acct):
            plan.errors.append(
                StripeMappingErrorItem(row_ref, "stripe_id_format", "stripe_account_id must look like acct_...")
            )
            continue
        if not (row.get("old_driver_id") or "").strip() and not (row.get("phone") or "").strip():
            plan.errors.append(
                StripeMappingErrorItem(row_ref, "csv", "row has neither old_driver_id nor phone to match on")
            )
            continue
        candidates.append((idx, row, row_ref, acct))

    candidates = _check_duplicate_stripe_ids(candidates, plan)
    by_old_id, by_phone, by_acct = _prefetch_drivers([row for _, row, _, _ in candidates])

    for _idx, row, row_ref, acct in candidates:
        old_id = (row.get("old_driver_id") or "").strip()
        phone = normalize_phone(row.get("phone") or "") if (row.get("phone") or "").strip() else ""
        matched_by_old = by_old_id.get(old_id) if old_id else None
        matched_by_phone = by_phone.get(phone) if phone else None

        if matched_by_old and matched_by_phone and matched_by_old["id"] != matched_by_phone["id"]:
            plan.errors.append(
                StripeMappingErrorItem(
                    row_ref, "ambiguous_match", "old_driver_id and phone resolve to different drivers"
                )
            )
            continue
        driver = matched_by_old or matched_by_phone
        if not driver:
            plan.errors.append(
                StripeMappingErrorItem(row_ref, "no_match", "no driver with this phone/old_driver_id found")
            )
            continue

        existing = driver.get("stripe_account_id")
        if existing == acct:
            plan.warnings.append(
                StripeMappingErrorItem(
                    row_ref, "already_mapped", "driver already carries this stripe_account_id; skipped"
                )
            )
            continue
        if existing:
            # Non-blocking: a driver who already has a DIFFERENT account must
            # not fail the batch — the rest of the CSV still commits. Surface it
            # for an explicit, confirmed per-driver update instead (redirecting a
            # payout destination is deliberate, never a bulk side effect). The
            # bulk commit's NULL-only guard also refuses to touch these rows.
            plan.needs_update.append(
                {
                    "row_ref": row_ref,
                    "driver_id": driver["id"],
                    "current_stripe_account_id": existing,
                    "new_stripe_account_id": acct,
                }
            )
            continue
        holder = by_acct.get(acct)
        if holder and holder["id"] != driver["id"]:
            plan.errors.append(
                StripeMappingErrorItem(row_ref, "id_taken", f"{acct} is already mapped to another driver")
            )
            continue

        old_acct = (row.get("old_stripe_account_id") or "").strip()
        if old_acct and not ACCT_RE.match(old_acct):
            plan.warnings.append(
                StripeMappingErrorItem(row_ref, "stripe_id_format", "old_stripe_account_id ignored (not acct_...)")
            )
            old_acct = ""
        plan.driver_updates.append(
            {
                "driver_id": driver["id"],
                "stripe_account_id": acct,
                "row_ref": row_ref,
                "old_stripe_account_id": old_acct or None,
                "existing_metadata": driver.get("legacy_import_metadata") or {},
            }
        )

    plan.driver_updates = _drop_duplicate_targets(plan, plan.driver_updates, "driver_id")
    plan.needs_update = _drop_duplicate_targets(plan, plan.needs_update, "driver_id")


def _build_local_rider_plan(rows: list[dict[str, str]], plan: StripeMappingPlan) -> None:
    candidates: list[tuple[int, dict[str, str], str, str]] = []
    for idx, row in enumerate(rows):
        row_ref = _row_ref(row, idx, "old_user_id")
        cus = (row.get("stripe_customer_id") or "").strip()
        if not CUS_RE.match(cus):
            plan.errors.append(
                StripeMappingErrorItem(row_ref, "stripe_id_format", "stripe_customer_id must look like cus_...")
            )
            continue
        if not (row.get("phone") or "").strip() and not (row.get("email") or "").strip():
            plan.errors.append(StripeMappingErrorItem(row_ref, "csv", "row has neither phone nor email to match on"))
            continue
        candidates.append((idx, row, row_ref, cus))

    candidates = _check_duplicate_stripe_ids(candidates, plan)
    by_phone, by_email, by_customer = _prefetch_users([row for _, row, _, _ in candidates])

    for _idx, row, row_ref, cus in candidates:
        phone = normalize_phone(row.get("phone") or "") if (row.get("phone") or "").strip() else ""
        email = (row.get("email") or "").strip().lower()
        matched_by_phone = by_phone.get(phone) if phone else None
        matched_by_email = by_email.get(email) if email else None

        if matched_by_phone and matched_by_email and matched_by_phone["id"] != matched_by_email["id"]:
            plan.errors.append(
                StripeMappingErrorItem(row_ref, "ambiguous_match", "phone and email resolve to different users")
            )
            continue
        user = matched_by_phone or matched_by_email
        if not user:
            plan.errors.append(StripeMappingErrorItem(row_ref, "no_match", "no user matches this row"))
            continue

        existing = user.get("stripe_customer_id")
        if existing == cus:
            plan.warnings.append(
                StripeMappingErrorItem(
                    row_ref, "already_mapped", "user already carries this stripe_customer_id; skipped"
                )
            )
            continue
        if existing:
            # Expected for riders who already used the new app — a customer was
            # lazily created on first payment. Operator drops the row; the
            # rider keeps the new-app customer and re-adds their card.
            plan.errors.append(
                StripeMappingErrorItem(
                    row_ref, "conflict_existing", "user already has a different stripe_customer_id; drop this row"
                )
            )
            continue
        holder = by_customer.get(cus)
        if holder and holder["id"] != user["id"]:
            plan.errors.append(StripeMappingErrorItem(row_ref, "id_taken", f"{cus} is already mapped to another user"))
            continue

        old_cus = (row.get("old_stripe_customer_id") or "").strip()
        if old_cus and not CUS_RE.match(old_cus):
            plan.warnings.append(
                StripeMappingErrorItem(row_ref, "stripe_id_format", "old_stripe_customer_id ignored (not cus_...)")
            )
            old_cus = ""
        plan.user_updates.append(
            {
                "user_id": user["id"],
                "stripe_customer_id": cus,
                "row_ref": row_ref,
                "old_stripe_customer_id": old_cus or None,
                "old_user_id": (row.get("old_user_id") or "").strip() or None,
                "existing_metadata": user.get("legacy_import_metadata") or {},
            }
        )

    plan.user_updates = _drop_duplicate_targets(plan, plan.user_updates, "user_id")


# ------------------------------------------------------ live Stripe phase


def _livemode_error(obj: dict[str, Any], stripe_secret: str) -> tuple[str, str] | None:
    """Hard error: a test-mode object must never be mapped as a live payout
    destination (or vice versa). Normally unreachable — a key can't retrieve
    the other mode's objects — so if it fires, something is deeply wrong."""
    live_key = stripe_secret.startswith("sk_live_")
    if bool(obj.get("livemode")) != live_key:
        return ("livemode", "object livemode does not match the configured key mode")
    return None


def _account_findings(account: dict[str, Any]) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Pure checks over a retrieved Connect account → (errors, warnings).

    Incomplete onboarding is a WARNING, not an error: preserving a
    half-onboarded account is exactly the point — the driver finishes the
    remaining requirements via the existing in-app AccountLink flow instead
    of starting over.
    """
    errors: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []

    country = account.get("country")
    if country != "CA":
        errors.append(("country", f"account country is {country}, expected CA"))

    transfers = (account.get("capabilities") or {}).get("transfers")
    if transfers is None:
        errors.append(("capability_transfers", "transfers capability was never requested on this account"))
    elif transfers != "active":
        warnings.append(("capability_transfers", f"transfers capability is {transfers}; driver must finish onboarding"))

    disabled = (account.get("requirements") or {}).get("disabled_reason") or ""
    if disabled.startswith("rejected") or disabled == "platform_paused":
        errors.append(("account_rejected", f"account is disabled ({disabled})"))

    if not account.get("details_submitted") or not account.get("payouts_enabled"):
        warnings.append(
            (
                "onboarding_incomplete",
                "details_submitted/payouts_enabled not yet true; driver finishes via in-app Stripe onboarding",
            )
        )
    if account.get("type") != "express":
        warnings.append(("account_type", f"account type is {account.get('type')}, not express"))
    if account.get("business_type") != "individual":
        warnings.append(("business_type", f"business_type is {account.get('business_type')}, not individual"))

    due = list((account.get("requirements") or {}).get("currently_due") or [])
    if due:
        # Requirement keys are Stripe field names (e.g. external_account), not PII.
        warnings.append(("requirements_due", "outstanding requirements: " + ", ".join(sorted(due)[:8])))
    return errors, warnings


def _customer_findings(
    customer: dict[str, Any], expected_user_id: str | None
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Pure checks over a retrieved Customer → (errors, warnings)."""
    errors: list[tuple[str, str]] = []
    warnings: list[tuple[str, str]] = []
    if customer.get("deleted"):
        errors.append(("customer_deleted", "customer is deleted in Stripe"))
    meta_uid = (customer.get("metadata") or {}).get("user_id")
    if meta_uid and expected_user_id and str(meta_uid) != str(expected_user_id):
        warnings.append(("metadata_user_mismatch", "Stripe customer metadata.user_id differs from the matched user"))
    return errors, warnings


async def _retrieve_stripe(
    retrieve: Any, obj_id: str, stripe_secret: str
) -> tuple[dict[str, Any] | None, tuple[str, str] | None]:
    """Retrieve one Stripe object off the event loop → (payload, error).

    Accessibility failures are the Scenario A/B litmus test: an ID from a
    different Stripe platform account errors here on every row. Transient
    Stripe errors block commit too (re-run validate) — never a silent pass.
    """
    import stripe

    try:
        obj = await asyncio.to_thread(retrieve, obj_id, api_key=stripe_secret)
    except (stripe.error.InvalidRequestError, stripe.error.PermissionError, stripe.error.AuthenticationError) as e:
        logger.error("[STRIPE-MAP] %s not accessible: %s", obj_id, e)
        return None, ("not_accessible", f"{obj_id} does not exist on this platform or is not accessible")
    except stripe.error.StripeError:
        logger.error("[STRIPE-MAP] transient Stripe error retrieving %s", obj_id, exc_info=True)
        return None, ("stripe_transient", "Stripe error while validating this row; re-run validate")
    fn = getattr(obj, "_to_dict_recursive", None) or getattr(obj, "to_dict_recursive", None)
    if callable(fn):
        try:
            return fn(), None
        except Exception:  # noqa: S110
            pass
    return dict(obj), None


async def build_plan(
    kind: str,
    rows: list[dict[str, str]],
    stripe_secret: str,
    batch: str,
    *,
    concurrency: int = MAX_STRIPE_CONCURRENCY,
) -> StripeMappingPlan:
    """Full plan: local matching/guards, then live Stripe validation.

    Never writes. Candidates that fail live validation are dropped from the
    update lists and recorded as errors, so ``commit_plan`` can trust that
    every remaining update points at a real, usable Stripe object.
    """
    plan = await asyncio.to_thread(_build_local_plan, kind, rows, batch)
    updates = plan.driver_updates if kind == KIND_DRIVERS else plan.user_updates
    if not updates:
        return plan
    if not stripe_secret:
        plan.errors.append(
            StripeMappingErrorItem("*", "stripe_not_configured", "stripe_secret_key is not set in app settings")
        )
        updates.clear()
        return plan

    import stripe

    retrieve = stripe.Account.retrieve if kind == KIND_DRIVERS else stripe.Customer.retrieve
    sem = asyncio.Semaphore(concurrency)

    async def check(upd: dict[str, Any]) -> dict[str, Any] | None:
        obj_id = upd.get("stripe_account_id") or upd["stripe_customer_id"]
        async with sem:
            payload, err = await _retrieve_stripe(retrieve, obj_id, stripe_secret)
        if err:
            plan.errors.append(StripeMappingErrorItem(upd["row_ref"], err[0], err[1]))
            return None
        if kind == KIND_DRIVERS:
            errs, warns = _account_findings(payload)
        else:
            errs, warns = _customer_findings(payload, upd.get("user_id"))
        livemode = _livemode_error(payload, stripe_secret)
        if livemode:
            errs = [*errs, livemode]
        for f, m in warns:
            plan.warnings.append(StripeMappingErrorItem(upd["row_ref"], f, m))
        for f, m in errs:
            plan.errors.append(StripeMappingErrorItem(upd["row_ref"], f, m))
        return None if errs else upd

    results = await asyncio.gather(*(check(u) for u in updates))
    kept = [u for u in results if u]
    if kind == KIND_DRIVERS:
        plan.driver_updates = kept
    else:
        plan.user_updates = kept
    return plan


# --------------------------------------------------------------- commit


def _provenance(plan: StripeMappingPlan, upd: dict[str, Any], now: str) -> dict[str, Any]:
    prov: dict[str, Any] = {"batch": plan.batch, "source": IMPORT_SOURCE, "imported_at": now}
    for key in ("old_stripe_account_id", "old_stripe_customer_id", "old_user_id"):
        if upd.get(key):
            prov[key] = upd[key]
    return prov


def _is_unique_violation(exc: Exception) -> bool:
    """Postgres 23505 via PostgREST/supabase-py — the migration-257 partial
    unique indexes firing because a concurrent commit took the same value."""
    return getattr(exc, "code", None) == "23505" or "duplicate key value" in str(exc)


def commit_plan(plan: StripeMappingPlan) -> dict[str, Any]:
    """Apply a clean plan. Sync (call via ``asyncio.to_thread`` from routes).

    Every update is guarded with ``.is_(<column>, "null")`` so the importer can
    only ever FILL an empty column — a value written by anyone else between
    validate and commit turns into a zero-row update, reported in
    ``conflicts`` rather than silently clobbered. That guard is also what
    makes the batch safely rollbackable (see the runbook).

    The migration-257 unique indexes cover the other half of the race: the
    same Stripe VALUE landing on two different rows via overlapping commits.
    A unique violation here is that guard firing — reported as a conflict,
    never retried onto another row. Any other DB error propagates (502).
    """
    if plan.errors:
        raise RuntimeError("plan has errors; refusing to commit")
    now = datetime.now(timezone.utc).isoformat()
    conflicts: list[str] = []

    updated_drivers = 0
    for upd in plan.driver_updates:
        meta = {**(upd.get("existing_metadata") or {}), "stripe_migration": _provenance(plan, upd, now)}
        try:
            res = (
                supabase.table("drivers")
                .update(
                    {
                        "stripe_account_id": upd["stripe_account_id"],
                        "legacy_import_metadata": meta,
                        "updated_at": now,
                    }
                )
                .eq("id", upd["driver_id"])
                .is_("stripe_account_id", "null")
                .execute()
            )
        except Exception as e:
            if _is_unique_violation(e):
                logger.error(
                    "[STRIPE-MAP] %s already taken by another driver row (concurrent commit)", upd["stripe_account_id"]
                )
                conflicts.append(upd["row_ref"])
                continue
            raise
        if res.data:
            updated_drivers += 1
        else:
            conflicts.append(upd["row_ref"])

    updated_users = 0
    for upd in plan.user_updates:
        meta = {**(upd.get("existing_metadata") or {}), "stripe_migration": _provenance(plan, upd, now)}
        try:
            res = (
                supabase.table("users")
                .update(
                    {
                        "stripe_customer_id": upd["stripe_customer_id"],
                        "legacy_import_metadata": meta,
                        "updated_at": now,
                    }
                )
                .eq("id", upd["user_id"])
                .is_("stripe_customer_id", "null")
                .execute()
            )
        except Exception as e:
            if _is_unique_violation(e):
                logger.error(
                    "[STRIPE-MAP] %s already taken by another user row (concurrent commit)", upd["stripe_customer_id"]
                )
                conflicts.append(upd["row_ref"])
                continue
            raise
        if res.data:
            updated_users += 1
        else:
            conflicts.append(upd["row_ref"])

    if conflicts:
        logger.error(
            "[STRIPE-MAP] batch=%s: %d row(s) hit commit-time conflicts (column filled since validate): %s",
            plan.batch,
            len(conflicts),
            conflicts,
        )
    return {"updated_drivers": updated_drivers, "updated_users": updated_users, "conflicts": conflicts}


def _driver_holding_account(acct: str) -> dict[str, Any] | None:
    """The single driver row currently carrying ``acct`` (or None)."""
    rows = (
        supabase.table("drivers").select("id,stripe_account_id").eq("stripe_account_id", acct).limit(1).execute().data
        or []
    )
    return rows[0] if rows else None


def _write_driver_account_update(
    driver_id: str, new_acct: str, expected_current_acct: str, meta: dict[str, Any], now: str
) -> bool:
    """Overwrite one driver's account, guarded on the expected current value
    (optimistic concurrency). Returns True iff exactly-this row was updated —
    zero rows means the value moved since we read it (stale review screen)."""
    res = (
        supabase.table("drivers")
        .update(
            {
                "stripe_account_id": new_acct,
                "legacy_import_metadata": meta,
                "updated_at": now,
                "stripe_account_onboarded": False,
                "stripe_details_submitted": False,
                "stripe_payouts_enabled": False,
                "stripe_id_number_provided": False,
                "stripe_requirements_due": [],
            }
        )
        .eq("id", driver_id)
        .eq("stripe_account_id", expected_current_acct)
        .execute()
    )
    return bool(res.data)


async def update_driver_stripe_account(
    driver_id: str,
    new_acct: str,
    expected_current_acct: str,
    batch: str,
    stripe_secret: str,
) -> dict[str, Any]:
    """Redirect ONE driver's payout account to ``new_acct`` (money-moving).

    Unlike ``commit_plan`` (which only fills NULL columns), this deliberately
    OVERWRITES an existing account, so it is per-driver, explicitly confirmed at
    the UI, and guarded three ways before it writes:

    - ``new_acct`` is live-validated against Stripe (Custom stays a warning;
      non-CA / transfers-never-requested / rejected are hard errors → refuse).
    - the write filters on ``stripe_account_id = expected_current_acct`` so a
      review screen that went stale can't clobber a newer value (→ ``stale``).
    - ``new_acct`` already held by another driver → ``id_taken`` (pre-check plus
      the migration-257 unique index as the concurrency backstop).

    Returns ``{ok, status, errors, warnings}`` where errors/warnings are
    ``(field, message)`` tuples — PII-free, same contract as plan items.
    ``status`` ∈ updated | validation_failed | stale | id_taken | not_found |
    bad_format | stripe_not_configured.
    """
    if not stripe_secret:
        return _update_result(
            False, "stripe_not_configured", [("stripe_not_configured", "stripe_secret_key is not set in app settings")]
        )
    if not ACCT_RE.match(new_acct or ""):
        return _update_result(False, "bad_format", [("stripe_id_format", "stripe_account_id must look like acct_...")])

    import stripe

    payload, err = await _retrieve_stripe(stripe.Account.retrieve, new_acct, stripe_secret)
    if err:
        return _update_result(False, "validation_failed", [err])
    errs, warns = _account_findings(payload)
    livemode = _livemode_error(payload, stripe_secret)
    if livemode:
        errs = [*errs, livemode]
    if errs:
        return _update_result(False, "validation_failed", errs, warns)

    def _write() -> dict[str, Any]:
        driver_rows = (
            supabase.table("drivers")
            .select("id,stripe_account_id,legacy_import_metadata")
            .eq("id", driver_id)
            .limit(1)
            .execute()
            .data
            or []
        )
        if not driver_rows:
            return _update_result(False, "not_found", warnings=warns)
        driver = driver_rows[0]
        if (driver.get("stripe_account_id") or "") != expected_current_acct:
            return _update_result(False, "stale", warnings=warns)
        holder = _driver_holding_account(new_acct)
        if holder and holder["id"] != driver_id:
            return _update_result(False, "id_taken", warnings=warns)
        now = datetime.now(timezone.utc).isoformat()
        meta = dict(driver.get("legacy_import_metadata") or {})
        migration = dict(meta.get("stripe_migration") or {})
        migration.update(
            {
                "batch": batch,
                "source": IMPORT_SOURCE,
                "action": "account_update",
                "updated_at": now,
                "previous_account_id": expected_current_acct,
            }
        )
        meta["stripe_migration"] = migration
        try:
            wrote = _write_driver_account_update(driver_id, new_acct, expected_current_acct, meta, now)
        except Exception as e:
            if _is_unique_violation(e):
                logger.error("[STRIPE-MAP] update: %s already taken by another driver (concurrent)", new_acct)
                return _update_result(False, "id_taken", warnings=warns)
            raise
        if not wrote:
            # Guarded write matched zero rows: current value moved between our
            # read and the update (rare race) → stale, operator re-validates.
            return _update_result(False, "stale", warnings=warns)
        logger.info(
            "[STRIPE-MAP] driver payout account updated batch=%s",
            batch,
            extra={"domain": "payments", "driver_id": driver_id},
        )
        return _update_result(True, "updated", warnings=warns)

    return await asyncio.to_thread(_write)


def _update_result(
    ok: bool, status: str, errors: list[tuple[str, str]] | None = None, warnings: list[tuple[str, str]] | None = None
) -> dict[str, Any]:
    return {"ok": ok, "status": status, "errors": errors or [], "warnings": warnings or []}


async def sync_kyc_after_commit(
    driver_ids: list[str],
    batch: str,
    *,
    concurrency: int = 5,
    expected_account_id: str | None = None,
) -> dict[str, Any]:
    """Mirror real KYC state from Stripe for every driver just mapped.

    CSV flags are never trusted — ``refresh_driver_kyc`` retrieves the live
    Account and populates the migration-92 mirror columns, exactly like the
    admin "Refresh from Stripe" button. Per-driver failures are logged and
    recorded (never halt the batch); the outcome is stamped into
    ``legacy_import_metadata.stripe_migration.kyc_sync`` so the status
    endpoint can show convergence.

    When ``expected_account_id`` is set (per-driver update path), skip the
    driver if their account changed between the update and this task — avoids
    writing KYC data from a superseded account after a rapid double-redirect.
    """
    try:
        from .. import db_supabase
        from . import stripe_kyc_sync
    except ImportError:  # pragma: no cover - allow direct/CLI module imports
        import db_supabase  # type: ignore
        from services import stripe_kyc_sync  # type: ignore

    sem = asyncio.Semaphore(concurrency)
    ok: list[str] = []
    failed: list[str] = []

    async def one(driver_id: str) -> None:
        async with sem:
            try:
                rows = await db_supabase.get_rows("drivers", {"id": driver_id}, limit=1)
                if not rows:
                    logger.error("[STRIPE-MAP] kyc sync: driver %s not found after commit", driver_id)
                    failed.append(driver_id)
                    return
                driver = rows[0]
                if expected_account_id and driver.get("stripe_account_id") != expected_account_id:
                    logger.warning(
                        "[STRIPE-MAP] kyc sync: driver %s account changed (%s → expected %s); skipping",
                        driver_id,
                        driver.get("stripe_account_id"),
                        expected_account_id,
                    )
                    failed.append(driver_id)
                    return
                result = await stripe_kyc_sync.refresh_driver_kyc(driver)
                status = result.get("status", "unknown")
                meta = dict(driver.get("legacy_import_metadata") or {})
                migration = dict(meta.get("stripe_migration") or {})
                migration["kyc_sync"] = status
                migration["kyc_synced_at"] = datetime.now(timezone.utc).isoformat()
                meta["stripe_migration"] = migration
                await db_supabase.update_one("drivers", {"id": driver_id}, {"legacy_import_metadata": meta})
                if status == "ok":
                    ok.append(driver_id)
                else:
                    logger.error("[STRIPE-MAP] kyc sync for driver %s returned %s", driver_id, status)
                    failed.append(driver_id)
            except Exception:
                logger.error("[STRIPE-MAP] kyc sync crashed for driver %s", driver_id, exc_info=True)
                failed.append(driver_id)

    await asyncio.gather(*(one(d) for d in driver_ids))
    logger.info(
        "[STRIPE-MAP] kyc sync batch=%s ok=%d failed=%d",
        batch,
        len(ok),
        len(failed),
        extra={"domain": "payments"},
    )
    return {"ok": len(ok), "failed": len(failed), "failed_driver_ids": failed}


# ------------------------------------------------------ email discovery

# The refresh/KYC tooling follows drivers.stripe_account_id and never guesses,
# so an operator who can SEE a driver's account in the Stripe dashboard (same
# email) still gets "no_stripe_account" until the column is filled. This is
# the bridge: read-only matching of unlinked drivers to connected accounts by
# email, emitting the exact CSV the validated import consumes. All WRITES stay
# in the import's validate/commit path — discovery never touches a row, so a
# wrong guess here can cost at most a rejected CSV row, never a payout.

def _list_connected_accounts(stripe_secret: str, cap: int = 1000) -> list[dict[str, Any]]:
    """Every connected account on the running key (Stripe has no email filter
    on /v1/accounts, so we page and match locally). Blocking; call in a thread.
    Capped defensively — a platform with more accounts than the cap gets a
    loud error rather than silent partial matching."""
    import stripe

    out: list[dict[str, Any]] = []
    for acct in stripe.Account.list(limit=100, api_key=stripe_secret).auto_paging_iter():
        d = dict(acct)
        out.append(
            {
                "id": d.get("id"),
                "email": (d.get("email") or "").strip().lower(),
                "country": d.get("country"),
                "type": d.get("type"),
                "details_submitted": bool(d.get("details_submitted")),
                "payouts_enabled": bool(d.get("payouts_enabled")),
                "created": d.get("created"),
            }
        )
        if len(out) > cap:
            raise RuntimeError(f"more than {cap} connected accounts; refusing to match partially")
    return out


def _unlinked_drivers_with_emails() -> list[dict[str, Any]]:
    """Drivers with no stripe_account_id, with an email resolved from the
    driver row or, failing that, their users row. Blocking; call in a thread."""
    drivers = (
        supabase.table("drivers")
        .select("id,phone,email,user_id,stripe_account_id,stripe_account_id_superseded")
        .is_("stripe_account_id", "null")
        .execute()
        .data
        or []
    )
    missing = [d["user_id"] for d in drivers if not (d.get("email") or "").strip() and d.get("user_id")]
    user_emails: dict[str, str] = {}
    if missing:
        for u in _select_in("users", "id,email", "id", sorted(set(missing))):
            if (u.get("email") or "").strip():
                user_emails[u["id"]] = u["email"]
    for d in drivers:
        email = (d.get("email") or "").strip() or user_emails.get(d.get("user_id") or "", "")
        d["email"] = email.lower()
    return drivers


async def discover_driver_accounts_by_email(stripe_secret: str) -> dict[str, Any]:
    """Propose driver ↔ connected-account links by exact email match.

    Matching criteria, deliberately strict:
      - exact, case-insensitive email equality — no fuzzy/name matching;
      - the account must not already be linked to ANY driver row;
      - one driver ↔ one account. An email seen on several accounts, or an
        account whose email matches several drivers, is reported under
        ``ambiguous`` for a human to resolve — never auto-proposed.

    Returns ``matches`` (with the account's live state so the operator can eye
    it), ``ambiguous``, ``unmatched_drivers``/``unmatched_accounts`` counts,
    and ``csv`` — a ``stripe_account_id,phone`` document ready for the
    existing ``/api/admin/stripe/import`` validate → commit flow, which
    re-validates every row live against Stripe and only ever fills NULL
    columns. Discovery itself writes nothing.
    """
    drivers, accounts = await asyncio.gather(
        asyncio.to_thread(_unlinked_drivers_with_emails),
        asyncio.to_thread(_list_connected_accounts, stripe_secret),
    )

    linked_accts: set[str] = set()
    already = await asyncio.to_thread(
        lambda: supabase.table("drivers").select("stripe_account_id").not_.is_("stripe_account_id", "null").execute()
    )
    for row in already.data or []:
        if row.get("stripe_account_id"):
            linked_accts.add(row["stripe_account_id"])

    accounts = [a for a in accounts if a["id"] not in linked_accts]

    # Lower-case at match time, not just in the fetchers — the equality
    # invariant lives HERE, so a future caller feeding raw emails cannot
    # silently miss matches.
    drivers_by_email: dict[str, list[dict[str, Any]]] = {}
    for d in drivers:
        email = (d.get("email") or "").strip().lower()
        if email:
            drivers_by_email.setdefault(email, []).append(d)
    accounts_by_email: dict[str, list[dict[str, Any]]] = {}
    for a in accounts:
        email = (a.get("email") or "").strip().lower()
        if email:
            accounts_by_email.setdefault(email, []).append(a)

    matches: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    for email, ds in sorted(drivers_by_email.items()):
        accts = accounts_by_email.get(email) or []
        if not accts:
            continue
        if len(ds) == 1 and len(accts) == 1:
            d, a = ds[0], accts[0]
            matches.append(
                {
                    "driver_id": d["id"],
                    "stripe_account_id": a["id"],
                    "matched_on": "email",
                    # Live account state so the operator can eyeball sanity
                    # before importing. The import re-validates regardless.
                    "account_country": a["country"],
                    "account_type": a["type"],
                    "details_submitted": a["details_submitted"],
                    "payouts_enabled": a["payouts_enabled"],
                    # Superseded set means this driver was retired by the
                    # key-mode repair — expected after a cutover; flagged so
                    # the operator understands why the slot is empty.
                    "was_retired": bool(d.get("stripe_account_id_superseded")),
                    "phone": normalize_phone(d.get("phone") or ""),
                }
            )
        else:
            ambiguous.append(
                {
                    "email_drivers": [d["id"] for d in ds],
                    "email_accounts": [a["id"] for a in accts],
                    "reason": "same email on multiple drivers and/or multiple accounts",
                }
            )

    csv_rows = ["stripe_account_id,phone"]
    csv_rows += [f"{m['stripe_account_id']},{m['phone']}" for m in matches if m["phone"]]
    phoneless = [m["driver_id"] for m in matches if not m["phone"]]

    return {
        "matches": matches,
        "ambiguous": ambiguous,
        "matched": len(matches),
        "unmatched_drivers": len([e for e in drivers_by_email if e not in accounts_by_email])
        + len([d for d in drivers if not (d.get("email") or "").strip()]),
        "unmatched_accounts": len([e for e in accounts_by_email if e not in drivers_by_email]),
        # A match whose driver row has no phone can't ride the CSV (the import
        # matches on old_driver_id/phone) — surfaced instead of dropped.
        "matches_without_phone": phoneless,
        "csv": "\n".join(csv_rows) + "\n" if len(csv_rows) > 1 else "",
    }
