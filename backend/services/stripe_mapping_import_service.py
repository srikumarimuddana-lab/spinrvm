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

    Returns (by_old_id, by_phone, by_stripe_account_id). BOTH match maps only
    include drivers stamped by the legacy driver importer (source match) —
    this tool exists solely for the legacy-imported cohort, and an unscoped
    phone match would let a mapping CSV point ANY not-yet-onboarded driver's
    payout destination at an arbitrary accessible Connect account. by_acct is
    deliberately unscoped: it detects value collisions across the whole table.
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
            if (d.get("legacy_import_metadata") or {}).get("source") != DRIVER_IMPORT_SOURCE:
                continue
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
                StripeMappingErrorItem(row_ref, "no_match", "no legacy-imported driver matches this row")
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
            plan.errors.append(
                StripeMappingErrorItem(
                    row_ref, "conflict_existing", "driver already has a different stripe_account_id; resolve manually"
                )
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
    try:
        return obj.to_dict_recursive(), None  # type: ignore[attr-defined]
    except AttributeError:
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


async def sync_kyc_after_commit(driver_ids: list[str], batch: str, *, concurrency: int = 5) -> dict[str, Any]:
    """Mirror real KYC state from Stripe for every driver just mapped.

    CSV flags are never trusted — ``refresh_driver_kyc`` retrieves the live
    Account and populates the migration-92 mirror columns, exactly like the
    admin "Refresh from Stripe" button. Per-driver failures are logged and
    recorded (never halt the batch); the outcome is stamped into
    ``legacy_import_metadata.stripe_migration.kyc_sync`` so the status
    endpoint can show convergence.
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
