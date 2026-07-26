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

import logging
import re
from collections import Counter
from dataclasses import dataclass, field
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

    Returns (by_old_id, by_phone, by_stripe_account_id). by_old_id only
    includes drivers stamped by the legacy driver importer (source match), so
    an unrelated driver can never be matched through a recycled old ID.
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
            plan.errors.append(StripeMappingErrorItem(row_ref, "no_match", "no imported driver matches this row"))
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
