// Legacy/bulk CSV import flows: drivers, historical bookings, Stripe ID
// mapping, riders — all super-admin-only. Extracted from the monolithic
// lib/api.ts as part of the per-domain split.

import { request } from "./client";

/* ── Bulk Driver Import (CSV) ─────────────── */
export interface DriverImportReportItem {
    old_driver_id: string;
    field: string;
    message: string;
}
export interface DriverImportReport {
    batch: string;
    can_commit: boolean;
    counts: { rows: number; users: number; drivers: number; updated: number; skipped_resume: number };
    warnings: DriverImportReportItem[];
    errors: DriverImportReportItem[];
    // Proves a /validate call happened for this exact (batch, CSV bytes,
    // admin) — /commit requires it back. See corporate + admin portal
    // review, gap #45. Optional: the locally-reconstructed "commit was
    // refused, here's why" report (built from DriverImportCommitResult
    // when the backend re-validation fails) never carries one — that's
    // fine, since the fix is always to re-validate anyway, which mints
    // a fresh token.
    validation_token?: string;
}
export interface DriverImportCommitResult {
    batch: string;
    committed: boolean;
    imported_users?: number;
    imported_drivers?: number;
    updated_drivers?: number;
    warnings?: DriverImportReportItem[];
    // Present (with can_commit=false) when the commit was refused on errors.
    can_commit?: boolean;
    counts?: DriverImportReport["counts"];
    errors?: DriverImportReportItem[];
}
export interface DriverImportOptions {
    serviceAreaId?: string;
    serviceAreaName?: string;
    batch?: string;
    // Required for /commit (gap #45) — pass report.validation_token from
    // the preceding /validate call. Omitted for /validate itself.
    validationToken?: string;
}

function driverImportFormData(file: File, opts?: DriverImportOptions): FormData {
    const fd = new FormData();
    fd.append("drivers_csv", file);
    if (opts?.serviceAreaId) fd.append("service_area_id", opts.serviceAreaId);
    if (opts?.serviceAreaName) fd.append("service_area_name", opts.serviceAreaName);
    if (opts?.batch) fd.append("batch", opts.batch);
    if (opts?.validationToken) fd.append("validation_token", opts.validationToken);
    return fd;
}

/** Dry-run: parse + validate the drivers CSV and return the report (no writes). */
export const adminValidateDriverImport = (file: File, opts?: DriverImportOptions) =>
    request<DriverImportReport>("/api/admin/drivers/import/validate", {
        method: "POST",
        body: driverImportFormData(file, opts),
    });

/** Commit the import. Returns committed=false + errors if the CSV no longer validates. */
export const adminCommitDriverImport = (file: File, opts?: DriverImportOptions) =>
    request<DriverImportCommitResult>("/api/admin/drivers/import/commit", {
        method: "POST",
        body: driverImportFormData(file, opts),
    });

/* ── Legacy Mongo Driver Import (raw export CSV) ──── */
// Super-admin-only (backend/routes/admin/legacy_driver_import.py). A
// SEPARATE importer from Bulk Driver Import above: this one reads the raw
// legacy Mongo export's drivers.csv (not the bespoke Saskatoon recruitment
// CSV) and creates/links/enriches accounts for the ~900 driver profiles that
// predate Spinr's current driver population. Same validate/commit-token
// contract as Bulk Driver Import (reuses the identical backend token
// mechanism, bound independently per endpoint), different response shape:
// a row either creates a NEW driver, LINKS a new driver to an existing
// account (no driver row yet), or ENRICHES an existing driver's history
// (no new row, no live field touched) — see counts below.
export interface LegacyDriverImportReportItem {
    old_driver_id: string;
    field: string;
    message: string;
}
export interface LegacyDriverImportCounts {
    rows: number;
    new_users: number;
    new_drivers: number;
    linked_accounts: number;
    enriched_drivers: number;
    skipped_resume: number;
}
export interface LegacyDriverImportReport {
    batch: string;
    can_commit: boolean;
    counts: LegacyDriverImportCounts;
    warnings: LegacyDriverImportReportItem[];
    errors: LegacyDriverImportReportItem[];
    // Proves a /validate call happened for this exact (batch, CSV bytes,
    // admin) — /commit requires it back, same gap-#45-shaped guarantee as
    // Bulk Driver Import's own token. Optional: a locally-reconstructed
    // "commit was refused, here's why" report never carries one — the fix
    // is always to re-validate, which mints a fresh token.
    validation_token?: string;
}
export interface LegacyDriverImportCommitResult {
    batch: string;
    committed: boolean;
    new_users?: number;
    new_drivers?: number;
    linked_accounts?: number;
    enriched_drivers?: number;
    warnings?: LegacyDriverImportReportItem[];
    // Present (with can_commit=false) when the commit was refused on errors.
    can_commit?: boolean;
    counts?: LegacyDriverImportCounts;
    errors?: LegacyDriverImportReportItem[];
}
export interface LegacyDriverImportOptions {
    serviceAreaId?: string;
    serviceAreaName?: string;
    batch?: string;
    // Required for /commit — pass report.validation_token from the
    // preceding /validate call. Omitted for /validate itself.
    validationToken?: string;
}

function legacyDriverImportFormData(file: File, opts?: LegacyDriverImportOptions): FormData {
    const fd = new FormData();
    fd.append("drivers_csv", file);
    if (opts?.serviceAreaId) fd.append("service_area_id", opts.serviceAreaId);
    if (opts?.serviceAreaName) fd.append("service_area_name", opts.serviceAreaName);
    if (opts?.batch) fd.append("batch", opts.batch);
    if (opts?.validationToken) fd.append("validation_token", opts.validationToken);
    return fd;
}

/** Dry-run: parse + validate the raw Mongo-export drivers.csv and return the
 * report (no writes). */
export const adminValidateLegacyDriverImport = (file: File, opts?: LegacyDriverImportOptions) =>
    request<LegacyDriverImportReport>("/api/admin/legacy-drivers/import/validate", {
        method: "POST",
        body: legacyDriverImportFormData(file, opts),
    });

/** Commit the import. Returns committed=false + errors if the CSV no longer validates. */
export const adminCommitLegacyDriverImport = (file: File, opts?: LegacyDriverImportOptions) =>
    request<LegacyDriverImportCommitResult>("/api/admin/legacy-drivers/import/commit", {
        method: "POST",
        body: legacyDriverImportFormData(file, opts),
    });

/** One-time repair for the 2026-08-29 production incident: users flagged
 * is_driver=true via this import's existing-account-link path whose
 * companion drivers row never landed (a since-fixed commit-ordering bug).
 * apply=false (default) only reports what would be created. */
export interface OrphanedDriverBackfillResult {
    scanned: number;
    applied: boolean;
    fixed: number;
}
export const adminBackfillOrphanedLegacyDrivers = (
    apply: boolean,
    opts?: { serviceAreaId?: string; serviceAreaName?: string },
) =>
    request<OrphanedDriverBackfillResult>("/api/admin/legacy-drivers/backfill-orphaned", {
        method: "POST",
        body: JSON.stringify({
            apply,
            service_area_id: opts?.serviceAreaId,
            service_area_name: opts?.serviceAreaName,
        }),
        headers: { "Content-Type": "application/json" },
    });

/** One-time repair (2026-08-30): the orphan backfill above stamps a
 * repaired drivers row's created_at as the repair run's own time; the
 * driver's real join date is already correct on their linked user's
 * created_at. apply=false (default) only reports what would change. */
export interface DriverCreatedAtBackfillResult {
    scanned: number;
    applied: boolean;
    fixed: number;
}
export const adminBackfillDriverCreatedAt = (apply: boolean) =>
    request<DriverCreatedAtBackfillResult>("/api/admin/legacy-drivers/backfill-created-at", {
        method: "POST",
        body: JSON.stringify({ apply }),
        headers: { "Content-Type": "application/json" },
    });

/* ── Legacy Booking Import (4 CSVs) ───────── */
// Super-admin-only (backend/routes/admin/booking_import.py). Imports completed
// rides from the previous app into `rides`, plus one offsetting `payouts` row
// per driver so already-settled earnings never become withdrawable again.
// Takes four files in one request — customers/drivers supply the phone numbers
// used to match Spinr accounts, and driverearnings supplies each booking's
// actual payout, which is what the offset must cancel.
export interface BookingImportReportItem {
    row_num: number;
    booking_code: string;
    field: string;
    message: string;
}
/** Mirrors BookingImportPlan.stats — all ints except the sum_* money fields. */
export interface BookingImportCounts {
    bookings_read: number;
    skipped_not_completed: number;
    skipped_test_account: number;
    skipped_cancelled_failed_excluded_by_scope: number;
    target_rows: number;
    skipped_already_imported: number;
    skipped_unmatched_both: number;
    rides_planned: number;
    unmatched_riders: number;
    unmatched_drivers: number;
    earnings_fallback_rows: number;
    missing_start_rows: number;
    payouts_planned: number;
    payouts_skipped_existing: number;
    drivers_to_recount: number;
    sum_rider_paid: number;
    sum_driver_total: number;
    sum_offset_payouts: number;
    sum_tips: number;
    sum_tax: number;
    // --- cancelled/failed path (added 2026-08-20, A41) --- rides_planned
    // above is completed-only and predates this path; these are the fields
    // that account for the rest of what commit actually writes to `rides`.
    cancelled_target_rows: number;
    failed_target_rows: number;
    cancelled_failed_rides_planned: number;
    cancelled_failed_skipped_already_imported: number;
    cancelled_failed_skipped_unmatched_both: number;
    cancelled_failed_zero_fare_completed: number;
    cancelled_failed_skipped_missing_coordinates: number;
    cancelled_failed_unmatched_riders: number;
    cancelled_failed_unmatched_drivers: number;
    total_rides_planned: number;
    insurance_periods_planned: number;
}
export interface BookingImportReport {
    batch: string;
    can_commit: boolean;
    counts: BookingImportCounts;
    warnings: BookingImportReportItem[];
    errors: BookingImportReportItem[];
}
export interface BookingImportCommitResult {
    batch: string;
    committed: boolean;
    imported_rides?: number;
    offset_payouts?: number;
    drivers_recounted?: number;
    counts?: BookingImportCounts;
    warnings?: BookingImportReportItem[];
    // Present (with can_commit=false) when the commit was refused.
    can_commit?: boolean;
    errors?: BookingImportReportItem[];
}
export interface BookingImportFiles {
    bookings: File;
    customers: File;
    drivers: File;
    earnings: File;
}
export interface BookingImportOptions {
    serviceAreaId?: string;
    serviceAreaName?: string;
    vehicleTypeId?: string;
    vehicleTypeName?: string;
    batch?: string;
    // Per-run scope, not a re-litigation of the 2026-08-20 decision to
    // support cancelled/failed bookings -- that decision stands and the
    // backend still defaults to true. Lets one commit be scoped down
    // without changing the default for the next.
    includeCancelledFailed?: boolean;
}

function bookingImportFormData(files: BookingImportFiles, opts?: BookingImportOptions): FormData {
    const fd = new FormData();
    fd.append("bookings_csv", files.bookings);
    fd.append("customers_csv", files.customers);
    fd.append("drivers_csv", files.drivers);
    fd.append("earnings_csv", files.earnings);
    if (opts?.serviceAreaId) fd.append("service_area_id", opts.serviceAreaId);
    if (opts?.serviceAreaName) fd.append("service_area_name", opts.serviceAreaName);
    if (opts?.vehicleTypeId) fd.append("vehicle_type_id", opts.vehicleTypeId);
    if (opts?.vehicleTypeName) fd.append("vehicle_type_name", opts.vehicleTypeName);
    if (opts?.batch) fd.append("batch", opts.batch);
    if (opts?.includeCancelledFailed !== undefined) {
        fd.append("include_cancelled_failed", String(opts.includeCancelledFailed));
    }
    return fd;
}

/** Dry-run: parse + validate the four CSVs and return the report (no writes). */
export const adminValidateBookingImport = (files: BookingImportFiles, opts?: BookingImportOptions) =>
    request<BookingImportReport>("/api/admin/bookings/import/validate", {
        method: "POST",
        body: bookingImportFormData(files, opts),
    });

/**
 * Commit the import. Returns committed=false + errors if the CSVs no longer
 * validate, or if there is nothing left to import (a completed re-run).
 * Pass the batch from the validate response so payout IDs stay deterministic.
 */
export const adminCommitBookingImport = (files: BookingImportFiles, opts?: BookingImportOptions) =>
    request<BookingImportCommitResult>("/api/admin/bookings/import/commit", {
        method: "POST",
        body: bookingImportFormData(files, opts),
    });

/* ── Legacy Wallet-Balance Import (3 CSVs) ── */
// Super-admin-only (backend/routes/admin/wallet_import.py). Imports prepaid
// rider/driver wallet credits from the previous app's `wallets` collection.
// Applies real money via the row-locked wallet_apply_delta RPC — never a
// plain balance write. Takes three files: wallets, customers, drivers —
// customers/drivers supply the phone numbers used to match each wallet row
// to an existing Spinr account.
export interface WalletImportReportItem {
    row_num: number;
    old_id: string;
    field: string;
    message: string;
}
/** Mirrors WalletImportPlan.stats — all ints except the sum_* money fields. */
export interface WalletImportCounts {
    rows_read: number;
    skipped_missing_id: number;
    skipped_duplicate_id: number;
    skipped_unmatched: number;
    skipped_zero_amount: number;
    target_rows: number;
    rider_rows: number;
    driver_rows: number;
    sum_add: number;
    sum_deduct: number;
    sum_net: number;
}
export interface WalletImportReport {
    batch: string;
    can_commit: boolean;
    counts: WalletImportCounts;
    warnings: WalletImportReportItem[];
    errors: WalletImportReportItem[];
}
/** One row per planned delta, in plan order — present only on a committed response. */
export interface WalletImportDeltaResult {
    reference_id: string;
    status: "applied" | "deduped" | "failed";
    transaction_id?: string;
    balance_after?: string;
    applied_delta?: string;
}
export interface WalletImportCommitResult {
    batch: string;
    committed: boolean;
    applied?: number;
    deduped?: number;
    failed?: number;
    results?: WalletImportDeltaResult[];
    counts?: WalletImportCounts;
    warnings?: WalletImportReportItem[];
    // Present (with can_commit=false) when the commit was refused on errors.
    can_commit?: boolean;
    errors?: WalletImportReportItem[];
}
export interface WalletImportFiles {
    wallets: File;
    customers: File;
    drivers: File;
}
export interface WalletImportOptions {
    batch?: string;
}

function walletImportFormData(files: WalletImportFiles, opts?: WalletImportOptions): FormData {
    const fd = new FormData();
    fd.append("wallets_csv", files.wallets);
    fd.append("customers_csv", files.customers);
    fd.append("drivers_csv", files.drivers);
    if (opts?.batch) fd.append("batch", opts.batch);
    return fd;
}

/** Dry-run: parse + validate the three CSVs and return the report (no writes). */
export const adminValidateWalletImport = (files: WalletImportFiles, opts?: WalletImportOptions) =>
    request<WalletImportReport>("/api/admin/wallets/import/validate", {
        method: "POST",
        body: walletImportFormData(files, opts),
    });

/**
 * Commit the import. Applies every planned delta via wallet_apply_delta.
 * Returns committed=false + errors if the CSVs no longer validate, or if
 * there is nothing left to import. Pass the batch from the validate response
 * so a re-send dedups via the RPC instead of double-crediting.
 */
export const adminCommitWalletImport = (files: WalletImportFiles, opts?: WalletImportOptions) =>
    request<WalletImportCommitResult>("/api/admin/wallets/import/commit", {
        method: "POST",
        body: walletImportFormData(files, opts),
    });

/* ── Legacy Stripe Mapping Import (CSV) ───── */
// Super-admin-only endpoints (backend/routes/admin/stripe_import.py). Maps
// old-app Stripe IDs onto imported rows: drivers.stripe_account_id (payout
// destination) / users.stripe_customer_id (saved cards).
export type StripeImportKind = "drivers" | "riders";
export interface StripeImportReportItem {
    row_ref: string;
    field: string;
    message: string;
}
// Drivers already carrying a DIFFERENT account than the CSV. Non-blocking:
// surfaced for an explicit per-driver update. PII-free (name is resolved in the
// UI by driver_id, never carried in the report).
export interface StripeImportNeedsUpdateItem {
    row_ref: string;
    driver_id: string;
    current_stripe_account_id: string;
    new_stripe_account_id: string;
}
export interface StripeImportReport {
    batch: string;
    kind: StripeImportKind;
    can_commit: boolean;
    counts: { rows: number; to_map: number; skipped_already_mapped: number; needs_update: number };
    warnings: StripeImportReportItem[];
    errors: StripeImportReportItem[];
    needs_update: StripeImportNeedsUpdateItem[];
}
export interface StripeImportCommitResult {
    batch: string;
    kind: StripeImportKind;
    committed: boolean;
    updated_drivers?: number;
    updated_users?: number;
    conflicts?: string[];
    kyc_sync?: "started" | "not_applicable";
    warnings?: StripeImportReportItem[];
    // Present (with can_commit=false) when the commit was refused on errors.
    can_commit?: boolean;
    counts?: StripeImportReport["counts"];
    errors?: StripeImportReportItem[];
    needs_update?: StripeImportNeedsUpdateItem[];
}
export interface StripeImportStatus {
    batch: string;
    drivers: number;
    kyc_sync: Record<string, number>;
    payouts_enabled: number;
    details_submitted: number;
}

function stripeImportFormData(file: File, kind: StripeImportKind, batch?: string): FormData {
    const fd = new FormData();
    fd.append("mapping_csv", file);
    fd.append("kind", kind);
    if (batch) fd.append("batch", batch);
    return fd;
}

export interface StripeDiscoveryMatch {
    driver_id: string;
    stripe_account_id: string;
    matched_on: "email";
    account_country: string | null;
    account_type: string | null;
    details_submitted: boolean;
    payouts_enabled: boolean;
    was_retired: boolean;
    phone: string;
}

export interface StripeDiscoveryReport {
    matches: StripeDiscoveryMatch[];
    ambiguous: { email_drivers: string[]; email_accounts: string[]; reason: string }[];
    matched: number;
    unmatched_drivers: number;
    unmatched_accounts: number;
    matches_without_phone: string[];
    csv: string;
}

/** Read-only: match unlinked drivers to connected Stripe accounts by exact
 * email and return proposals + a ready-to-import CSV. Writes nothing —
 * the CSV goes through validate → commit below like any hand-built one. */
export const adminDiscoverStripeDriverAccounts = () =>
    request<StripeDiscoveryReport>("/api/admin/stripe/import/discover", { method: "POST" });

/** Dry-run: parse, match, and live-validate the mapping CSV (no writes). */
export const adminValidateStripeImport = (file: File, kind: StripeImportKind, batch?: string) =>
    request<StripeImportReport>("/api/admin/stripe/import/validate", {
        method: "POST",
        body: stripeImportFormData(file, kind, batch),
    });

/** Commit the mapping. Returns committed=false + errors if the CSV no longer validates. */
export const adminCommitStripeImport = (file: File, kind: StripeImportKind, batch?: string) =>
    request<StripeImportCommitResult>("/api/admin/stripe/import/commit", {
        method: "POST",
        body: stripeImportFormData(file, kind, batch),
    });

/** Per-batch KYC-sync convergence (drivers kind only). DB-only, cheap to poll. */
export const adminStripeImportStatus = (batch: string) =>
    request<StripeImportStatus>(
        `/api/admin/stripe/import/status?batch=${encodeURIComponent(batch)}`,
    );

export interface StripeDriverAccountUpdateResult {
    ok: boolean;
    status: "updated";
    driver_id: string;
    batch: string;
    warnings: StripeImportReportItem[];
}

/**
 * Overwrite ONE driver's payout account (resolves a `needs_update` row). This
 * redirects where the driver is paid, so it is super-admin-only, live-validated,
 * and confirmed in the UI. On failure `request` throws with the backend's
 * human-readable message (409 = stale/id-taken, 422 = bad account/validation).
 */
export const adminUpdateDriverStripeAccount = (body: {
    driver_id: string;
    new_stripe_account_id: string;
    current_stripe_account_id: string;
    batch?: string;
}) =>
    request<StripeDriverAccountUpdateResult>("/api/admin/stripe/import/update-driver", {
        method: "POST",
        body: JSON.stringify(body),
    });

/* ── Bulk Rider Import (CSV) ──────────────── */
export interface RiderImportReportItem {
    row_num: number;
    field: string;
    message: string;
}
export interface RiderImportDuplicate {
    row: number;
    phone: string;
    // "protected_skip" (P0-C, docs/audit/2026-08-11-driver-rider-migration-audit.md):
    // the matched account is pending_deletion/deleted — no fields were
    // modified, flagged here for manual review rather than auto-updated.
    match_type: "rider" | "driver" | "protected_skip";
    is_driver: boolean;
    existing_user_id: string;
}
export interface RiderImportReport {
    batch: string;
    can_commit: boolean;
    counts: {
        rows: number;
        to_create: number;
        to_update: number;
        duplicates: number;
        duplicate_drivers: number;
        protected_skips: number;
    };
    duplicates: RiderImportDuplicate[];
    warnings: RiderImportReportItem[];
    errors: RiderImportReportItem[];
}
export interface RiderImportCommitResult {
    batch: string;
    committed: boolean;
    created_users?: number;
    updated_users?: number;
    duplicates?: RiderImportDuplicate[];
    warnings?: RiderImportReportItem[];
    can_commit?: boolean;
    counts?: RiderImportReport["counts"];
    errors?: RiderImportReportItem[];
}

function riderImportFormData(file: File, batch?: string): FormData {
    const fd = new FormData();
    fd.append("riders_csv", file);
    if (batch) fd.append("batch", batch);
    return fd;
}

/** Dry-run: parse + validate the riders CSV and return the report (no writes). */
export const adminValidateRiderImport = (file: File, batch?: string) =>
    request<RiderImportReport>("/api/admin/riders/import/validate", {
        method: "POST",
        body: riderImportFormData(file, batch),
    });

/** Commit the rider import. Returns committed=false + errors if the CSV no longer validates. */
export const adminCommitRiderImport = (file: File, batch?: string) =>
    request<RiderImportCommitResult>("/api/admin/riders/import/commit", {
        method: "POST",
        body: riderImportFormData(file, batch),
    });

/** One-time repair (2026-08-30): build_plan() previously hardcoded new
 * riders' created_at to import time instead of the CSV's own legacy value,
 * so already-imported riders show the wrong "Joined" date. Re-upload the
 * same rider CSV (or any CSV with the same phones + a real created_at
 * column) to find and fix the mismatch. apply=false (default) only reports
 * what would change. */
export interface RiderCreatedAtBackfillResult {
    scanned_rows: number;
    applied: boolean;
    fixed: number;
}
export const adminBackfillRiderCreatedAt = (file: File, apply: boolean) => {
    const fd = new FormData();
    fd.append("riders_csv", file);
    fd.append("apply", String(apply));
    return request<RiderCreatedAtBackfillResult>("/api/admin/riders/created-at-backfill", {
        method: "POST",
        body: fd,
    });
};

/* ── Legacy SIN/DOB Backfill (2 CSVs) ─────── */
// Admin-dashboard wrapper for the CLI-only
// backend/scripts/backfill_legacy_driver_sin_dob.py (Phase 2 of the
// 2026-08-27 migration plan). Writes vault-encrypted SIN + date_of_birth
// onto already-legacy-imported drivers, matched by phone via a two-file
// crosswalk: banks.csv (SIN/DOB keyed by Mongo driver_id) + drivers.csv
// (that export's driver collection, used only to resolve driver_id -> phone).
// Never clobbers a value already on file — see the backend route's
// docstring for the full safety/PIPEDA reasoning. Reports carry only
// old_driver_id/field/message — never a raw SIN or DOB.
export interface SinDobBackfillReportItem {
    old_driver_id: string;
    field: string;
    message: string;
}
export interface SinDobBackfillCounts {
    rows: number;
    to_update: number;
    skipped_unmatched: number;
    skipped_not_legacy_driver: number;
    skipped_already_on_file: number;
    skipped_duplicate_match: number;
}
export interface SinDobBackfillReport {
    batch: string;
    can_commit: boolean;
    counts: SinDobBackfillCounts;
    warnings: SinDobBackfillReportItem[];
    errors: SinDobBackfillReportItem[];
    // Proves a /validate call happened for this exact (batch, combined CSV
    // bytes, admin) — /commit requires it back. Mirrors the driver-import
    // gap #45 token.
    validation_token?: string;
}
export interface SinDobBackfillCommitResult {
    batch: string;
    committed: boolean;
    updated?: number;
    // old_driver_id refs whose write lost a plan/apply race to a driver
    // self-entering their own SIN/DOB in the meantime — never a value.
    conflicts?: string[];
    warnings?: SinDobBackfillReportItem[];
    // Present (with can_commit=false) when the commit was refused on errors.
    can_commit?: boolean;
    counts?: SinDobBackfillCounts;
    errors?: SinDobBackfillReportItem[];
}
export interface SinDobBackfillFiles {
    banks: File;
    drivers: File;
}
export interface SinDobBackfillOptions {
    batch?: string;
    // Required for /commit — pass report.validation_token from the
    // preceding /validate call. Omitted for /validate itself.
    validationToken?: string;
}

function sinDobBackfillFormData(files: SinDobBackfillFiles, opts?: SinDobBackfillOptions): FormData {
    const fd = new FormData();
    fd.append("banks_csv", files.banks);
    fd.append("drivers_csv", files.drivers);
    if (opts?.batch) fd.append("batch", opts.batch);
    if (opts?.validationToken) fd.append("validation_token", opts.validationToken);
    return fd;
}

/** Dry-run: parse + validate banks.csv + drivers.csv and return the report (no writes). */
export const adminValidateSinDobBackfill = (files: SinDobBackfillFiles, opts?: SinDobBackfillOptions) =>
    request<SinDobBackfillReport>("/api/admin/legacy-drivers/sin-dob-backfill/validate", {
        method: "POST",
        body: sinDobBackfillFormData(files, opts),
    });

/** Commit the backfill. Returns committed=false + errors if the CSVs no longer validate. */
export const adminCommitSinDobBackfill = (files: SinDobBackfillFiles, opts?: SinDobBackfillOptions) =>
    request<SinDobBackfillCommitResult>("/api/admin/legacy-drivers/sin-dob-backfill/commit", {
        method: "POST",
        body: sinDobBackfillFormData(files, opts),
    });

/* ── Imported Ride Snapshot Regeneration ─────────────── */
export interface SnapshotRegenerateResult {
    total: number;
    success: number;
    failed: number;
    renderer: string;
    errors: { ride_id: string; error: string }[];
}

export const adminRegenerateImportedSnapshots = (force: boolean, limit: number = 50) =>
    request<SnapshotRegenerateResult>("/api/admin/rides/regenerate-imported-snapshots", {
        method: "POST",
        body: JSON.stringify({ force, limit }),
        headers: { "Content-Type": "application/json" },
    });

/* ── Imported Ride Road-Route Regeneration ───────────── */
// Admin-dashboard equivalent of scripts/backfill_imported_ride_routes.py --
// that CLI script needs shell access to the backend; this lets an operator
// run the same backfill safely through the browser like every other
// legacy-migration tool on this page.
export interface RouteRegenerateResult {
    total: number;
    success: number;
    failed: number;
    message?: string;
    errors: { ride_id: string; error: string }[];
}

export const adminRegenerateImportedRoutes = (force: boolean, limit: number = 200) =>
    request<RouteRegenerateResult>("/api/admin/rides/regenerate-imported-routes", {
        method: "POST",
        body: JSON.stringify({ force, limit }),
        headers: { "Content-Type": "application/json" },
    });

/* ── Legacy Vehicle-History Backfill (2 CSVs) ── */
// Super-admin/drivers-module-gated (backend/routes/admin/legacy_vehicle_history_backfill.py).
// Phase 2 of the 2026-08-27 migration plan: backfills append-only
// driver_vehicle_history rows (regulatory audit table, migration 157) from
// the previous app's raw Mongo export. Takes two files — vehicle_details.csv
// (VIN/plate/make/model/colour/year, keyed by a Mongo ObjectId driver_id)
// and drivers.csv (the same export's driver collection, used only to
// resolve that ObjectId to a phone number). No live vehicle/driver field is
// ever mutated — only new history rows are appended.
export interface VehicleHistoryBackfillReportItem {
    old_driver_id: string;
    field: string;
    message: string;
}
export interface VehicleHistoryBackfillCounts {
    vehicle_rows: number;
    history_rows_to_insert: number;
    skipped_unmatched: number;
    skipped_not_legacy_driver: number;
    skipped_already_backfilled: number;
}
export interface VehicleHistoryBackfillReport {
    batch: string;
    can_commit: boolean;
    counts: VehicleHistoryBackfillCounts;
    warnings: VehicleHistoryBackfillReportItem[];
    errors: VehicleHistoryBackfillReportItem[];
    // Proves a /validate call happened for this exact (batch, both files'
    // bytes, admin) — /commit requires it back. The token binds
    // sha256(vehicle_details_bytes + "|" + drivers_bytes), so swapping
    // either file between validate and commit invalidates it. Optional: a
    // locally-reconstructed "commit was refused, here's why" report never
    // carries one — the fix is always to re-validate, which mints a fresh
    // token.
    validation_token?: string;
}
export interface VehicleHistoryBackfillCommitResult {
    batch: string;
    committed: boolean;
    history_rows_inserted?: number;
    warnings?: VehicleHistoryBackfillReportItem[];
    // Present (with can_commit=false) when the commit was refused on errors.
    can_commit?: boolean;
    counts?: VehicleHistoryBackfillCounts;
    errors?: VehicleHistoryBackfillReportItem[];
}
export interface VehicleHistoryBackfillFiles {
    vehicleDetails: File;
    drivers: File;
}
export interface VehicleHistoryBackfillOptions {
    batch?: string;
    // Required for /commit — pass report.validation_token from the
    // preceding /validate call. Omitted for /validate itself.
    validationToken?: string;
}

function vehicleHistoryBackfillFormData(files: VehicleHistoryBackfillFiles, opts?: VehicleHistoryBackfillOptions): FormData {
    const fd = new FormData();
    fd.append("vehicle_details_csv", files.vehicleDetails);
    fd.append("drivers_csv", files.drivers);
    if (opts?.batch) fd.append("batch", opts.batch);
    if (opts?.validationToken) fd.append("validation_token", opts.validationToken);
    return fd;
}

/** Dry-run: parse + validate both CSVs and return the report (no writes). */
export const adminValidateVehicleHistoryBackfill = (files: VehicleHistoryBackfillFiles, opts?: VehicleHistoryBackfillOptions) =>
    request<VehicleHistoryBackfillReport>("/api/admin/legacy-drivers/vehicle-history-backfill/validate", {
        method: "POST",
        body: vehicleHistoryBackfillFormData(files, opts),
    });

/** Commit the backfill. Returns committed=false + errors if the CSVs no longer validate. */
export const adminCommitVehicleHistoryBackfill = (files: VehicleHistoryBackfillFiles, opts?: VehicleHistoryBackfillOptions) =>
    request<VehicleHistoryBackfillCommitResult>("/api/admin/legacy-drivers/vehicle-history-backfill/commit", {
        method: "POST",
        body: vehicleHistoryBackfillFormData(files, opts),
    });

