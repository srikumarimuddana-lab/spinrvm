/**
 * Shared factory for Bulk Import tools' plain-language error/warning
 * explanations, generalized from lib/tax-id-error-help.ts (the Legacy Tax-ID
 * Backfill pilot). Each tool's backend emits its own vocabulary of short,
 * technical messages -- this factory builds a lookup (exact match, then
 * prefix match for messages carrying a dynamic suffix) so each tool exports
 * a small, tool-specific map instead of duplicating the lookup logic.
 *
 * Matching is against the exact strings each tool's backend route/service
 * emits; if a backend message changes, add/update the mapping here rather
 * than editing the backend to match this file.
 */

export interface IssueExplanation {
    /** One line: what actually happened, in plain language. */
    cause: string;
    /** One line: what to do about it. */
    fix: string;
}

export type IssueExplainer = (message: string) => IssueExplanation | null;

/** Builds an explainer from an exact-match map plus optional prefix rules
 * (for messages carrying a dynamic suffix, e.g. "invalid SIN, skipped: <reason>"). */
export function createIssueExplainer(
    exact: Record<string, IssueExplanation>,
    prefixes: [string, IssueExplanation][] = [],
): IssueExplainer {
    return (message: string) => {
        if (exact[message]) return exact[message];
        for (const [prefix, explanation] of prefixes) {
            if (message.startsWith(prefix)) return explanation;
        }
        return null;
    };
}

// Shared across every driver-side legacy backfill that resolves a Mongo
// driver_id -> phone via drivers.csv (SIN/DOB, Vehicle-History): the same
// join produces the same three skip reasons verbatim.
const DRIVER_CROSSWALK_EXACT: Record<string, IssueExplanation> = {
    "driver_id has no matching row in the Mongo drivers export": {
        cause: "This row's driver_id doesn't match any row in the drivers.csv file you uploaded.",
        fix: "Double-check you uploaded the drivers.csv from the same export as the other file -- a driver_id from a different export batch won't match.",
    },
    "no Spinr driver with this phone number": {
        cause: "The phone number resolved from drivers.csv doesn't match any driver already in Spinr.",
        fix: "This driver may not have gone through Legacy Driver Import yet, or the phone number differs between the old and new systems.",
    },
    "matched driver is not a known legacy-imported driver; skipped": {
        cause: "A Spinr driver has this phone number, but they weren't created by the legacy import -- this tool only ever touches legacy-imported drivers, so it left them alone.",
        fix: "This is expected and protective, not a bug -- it stops a phone-number coincidence from touching an organic driver's record. No action needed.",
    },
};

// ── Legacy SIN/DOB Backfill ─────────────────────────────────────────────
export const explainSinDobIssue: IssueExplainer = createIssueExplainer(
    {
        ...DRIVER_CROSSWALK_EXACT,
        "duplicate phone match within this batch; first row wins": {
            cause: "Two rows in this batch resolve to the same driver -- only the first one encountered is applied.",
            fix: "If the second row has different values, decide which is correct and re-run with only that one row for this driver.",
        },
        "could not parse date": {
            cause: "The date of birth value in banks.csv isn't in a format this tool recognizes.",
            fix: "Check the raw value in banks.csv for this row -- it may be blank, malformed, or use an unexpected date format.",
        },
    },
    [
        [
            "invalid SIN, skipped:",
            {
                cause: "This row's SIN doesn't pass basic validation (wrong length, starts with 0, repeated digits, or fails its checksum) -- the specific reason follows the colon in the message above.",
                fix: "Compare it against the original document (SIN card / previous app record) for a mistyped digit, or leave it blank if it's not actually available for this driver.",
            },
        ],
    ],
);

// ── Legacy Vehicle-History Backfill ─────────────────────────────────────
export const explainVehicleHistoryIssue: IssueExplainer = createIssueExplainer({
    ...DRIVER_CROSSWALK_EXACT,
    "missing or unparseable created_at": {
        cause: "This row's timestamp (when the vehicle detail was recorded) is missing or in an unexpected format.",
        fix: "Check the created_at value for this row in vehicle_details.csv -- without a valid timestamp, this tool can't place it in the vehicle's history in order.",
    },
});

// ── Legacy Saved-Address Backfill ───────────────────────────────────────
export const explainSavedAddressIssue: IssueExplainer = createIssueExplainer({
    "customer_addresses.csv is empty": {
        cause: "The customer_addresses.csv file has no data rows.",
        fix: "Check that the file wasn't saved empty or truncated during export.",
    },
    "customer_addresses.csv is missing required column": {
        cause: "customer_addresses.csv is missing a column this tool requires (customer_id, lat, long, or name).",
        fix: "Re-export the file and confirm its header row includes customer_id, lat, long, and name exactly.",
    },
    "no matching customer row in customers.csv": {
        cause: "This address's customer_id doesn't match any row in the customers.csv file you uploaded.",
        fix: "Double-check you uploaded the customers.csv from the same export batch as customer_addresses.csv.",
    },
    "address text is missing or an implausible length": {
        cause: "The address text for this row is empty, extremely short, or extremely long -- not a real address.",
        fix: "Check this row's address text in the source file; it may be blank or corrupted data.",
    },
    "no matching Spinr rider account": {
        cause: "The phone number resolved from customers.csv doesn't match any rider account already in Spinr.",
        fix: "This rider may not have gone through Bulk Rider Import yet, or the phone number differs between the old and new systems.",
    },
});

// ── Legacy Wallet-Balance Import ────────────────────────────────────────
export const explainWalletImportIssue: IssueExplainer = createIssueExplainer(
    {
        "wallets CSV is empty": {
            cause: "The wallets.csv file has no data rows.",
            fix: "Check that the file wasn't saved empty or truncated during export.",
        },
        "wallets CSV is missing required column": {
            cause: "wallets.csv is missing a column this tool requires.",
            fix: "Re-export the file and compare its header row against the tool's column-name caveat above -- these columns are inferred, not yet confirmed against a real export.",
        },
        "wallet entry is missing its legacy _id": {
            cause: "This row has no _id value, so there's nothing to key this wallet entry on.",
            fix: "Check the source file for a blank _id in this row.",
        },
        "duplicate legacy wallet entry _id in CSV": {
            cause: "Another row in this file already used this same _id.",
            fix: "Check wallets.csv for two rows sharing the same _id -- only one can be applied.",
        },
        "missing or unparseable created_at": {
            cause: "This wallet entry's timestamp is missing or in an unexpected format.",
            fix: "Check the created_at value for this row -- without it, this tool can't tell whether the entry predates launch.",
        },
        "pre-launch (before 2026-03-30); skipped as test data": {
            cause: "This wallet entry is dated before Spinr's launch -- it's test/seed data from before real money moved, not a real balance owed.",
            fix: "This is expected and correct to skip. No action needed.",
        },
        "no matching rider/driver account found": {
            cause: "This wallet entry's rider or driver hasn't been matched to an existing Spinr account.",
            fix: "The account may not be imported yet -- re-run this tool after that rider/driver has gone through their own import, and this entry will be picked up then.",
        },
        "amount is zero or unparseable, skipped": {
            cause: "This wallet entry's amount is zero, blank, or not a recognizable number.",
            fix: "Check the amount column for this row in wallets.csv.",
        },
    },
    [
        [
            "unrecognized legacy wallet type",
            {
                cause: "This row's wallet_type value doesn't match any type this tool knows how to translate into a Spinr transaction type.",
                fix: "Check the wallet_type value for this row against the previous app's known wallet-type values.",
            },
        ],
        [
            "unrecognized legacy wallet status",
            {
                cause: "This row's status value doesn't match any status this tool knows how to translate (credit vs. debit).",
                fix: "Check the status value for this row against the previous app's known wallet-status values.",
            },
        ],
    ],
);

// ── Legacy Booking Import ───────────────────────────────────────────────
export const explainBookingImportIssue: IssueExplainer = createIssueExplainer({
    "booking is missing its legacy _id": {
        cause: "This row has no _id value, so there's nothing to key this booking on.",
        fix: "Check the source file for a blank _id in this row.",
    },
    "duplicate legacy booking _id in CSV": {
        cause: "Another row in this file already used this same _id.",
        fix: "Check bookings.csv for two rows sharing the same _id -- only one can be applied.",
    },
    "missing or unparseable pickup/drop lat/lng": {
        cause: "This booking's pickup or drop-off coordinates are missing or not valid numbers.",
        fix: "Check the pickup/drop lat/lng columns for this row in bookings.csv.",
    },
    "missing or unparseable created_at": {
        cause: "This booking's creation timestamp is missing or in an unexpected format.",
        fix: "Check the created_at value for this row -- without it, this tool can't place the booking in time.",
    },
    "completed booking has no completion timestamp": {
        cause: "This booking is marked completed but has no completion timestamp, so this tool can't tell when the trip actually ended.",
        fix: "Check the complete_delivery_at value for this row in bookings.csv.",
    },
    "unparseable completion timestamp": {
        cause: "This booking's completion timestamp is present but not in a format this tool recognizes.",
        fix: "Check the complete_delivery_at value for this row in bookings.csv.",
    },
    "driver earning is smaller than the tip it should include": {
        cause: "The driver's total earning for this booking is less than the tip alone -- the numbers in this row don't add up.",
        fix: "Check the you_earn and tip_driver values for this row against the source data; this row can't be imported as-is.",
    },
    "fees + tax + tip exceed the total charged": {
        cause: "This booking's fees, tax, and tip together add up to more than the total amount charged -- the numbers in this row don't add up.",
        fix: "Check the total_amount, gst, and tip_driver values for this row against the source data.",
    },
    "legacy pickup address was blank": {
        cause: "This booking has no pickup address text in the source file.",
        fix: "This is imported anyway with a blank address -- no action needed unless you want to backfill the address by hand later.",
    },
    "legacy drop address was blank": {
        cause: "This booking has no drop-off address text in the source file.",
        fix: "This is imported anyway with a blank address -- no action needed unless you want to backfill the address by hand later.",
    },
    "no start timestamp; duration estimated from distance": {
        cause: "This booking has no ride-start timestamp, so its duration is estimated from distance instead of measured directly.",
        fix: "This is expected for some legacy bookings and is imported anyway with an estimated duration -- no action needed.",
    },
    "no legacy earnings row; using booking you_earn": {
        cause: "This booking has no matching row in driverearnings.csv, so this tool used the booking's own you_earn value instead.",
        fix: "This is a known, safe fallback for a small number of legacy bookings -- no action needed.",
    },
});

// ── Legacy Driver Import ────────────────────────────────────────────────
export const explainLegacyDriverImportIssue: IssueExplainer = createIssueExplainer(
    {
        "row has no _id": {
            cause: "This row has no _id value, so there's nothing to key this driver on.",
            fix: "Check the source file for a blank _id in this row.",
        },
        "duplicate _id": {
            cause: "Another row in this file already used this same _id.",
            fix: "Check drivers.csv for two rows sharing the same _id -- only one can be applied.",
        },
        "phone is not a valid 10-digit North American number": {
            cause: "This row's phone number isn't a recognizable 10-digit North American number.",
            fix: "Check the phone value for this row -- it may be missing digits, have a non-North-American format, or be blank.",
        },
        "email is not a valid format; imported without it": {
            cause: "This row's email address doesn't look like a valid email, so the driver was imported with no email instead of rejecting the whole row.",
            fix: "This is expected and non-blocking -- Spinr authenticates by phone, not email, so a missing email doesn't stop the driver from using the app. No action needed unless you want to add a corrected email later.",
        },
        "already imported/linked by a previous run of this importer": {
            cause: "This driver was already created or linked by an earlier run of this same import tool.",
            fix: "This is expected on a re-run of the same file and is not a problem -- no action needed.",
        },
    },
    [
        [
            "row has no name",
            {
                cause: "This row has a blank name in the source file -- on the previous app, that means the driver verified their phone but never finished setting up their profile, and never actually drove a trip.",
                fix: "This is imported anyway with a placeholder name and forced into needs_review status, so it's safely excluded from dispatch either way. No action needed.",
            },
        ],
        [
            "matches a driver created earlier in this same import batch",
            {
                cause: "Another row earlier in this same CSV already created a driver with this same phone number -- this row's history was merged into that one instead of creating a duplicate.",
                fix: "This is expected when the same driver appears more than once in the export and is not a problem. No action needed.",
            },
        ],
    ],
);

// ── Bulk Driver Import (Saskatoon recruitment CSV) ──────────────────────
export const explainDriverImportIssue: IssueExplainer = createIssueExplainer(
    {
        "drivers CSV is empty": {
            cause: "The uploaded file has no data rows.",
            fix: "Check that the file wasn't saved empty or truncated during export.",
        },
        "drivers CSV is missing required column": {
            cause: "The uploaded CSV is missing a column this tool requires.",
            fix: "Re-download the CSV template above and compare its header row against your file.",
        },
        "duplicate old_driver_id": {
            cause: "Another row in this file already used this same old_driver_id.",
            fix: "Check the file for two rows sharing the same old_driver_id -- only one can be applied.",
        },
        "row is not scoped to Saskatoon": {
            cause: "This row belongs to a different service area than this tool is scoped to.",
            fix: "This tool only imports Saskatoon drivers -- remove this row, or use the correct import for its service area.",
        },
        "phone is not a valid 10-digit North American number": {
            cause: "This row's phone number isn't a recognizable 10-digit North American number.",
            fix: "Check the phone value for this row -- it may be missing digits, have an international format, or be blank.",
        },
        "email is not a valid format": {
            cause: "This row's email address doesn't look like a valid email.",
            fix: "Check the email value for this row for a typo or missing @ symbol.",
        },
        "could not parse date": {
            cause: "This row's date of birth isn't in a format this tool recognizes.",
            fix: "Check the date_of_birth value for this row -- it may be blank, malformed, or use an unexpected date format.",
        },
        "matching user or driver already exists; handle manually before import": {
            cause: "A Spinr account already exists for this phone or email, but in a shape this tool can't safely reconcile automatically (e.g. it's not clearly the same person's existing driver profile).",
            fix: "Look up this phone/email in the admin Users/Drivers pages and decide by hand whether to link, skip, or correct the row before re-running.",
        },
        "VIN must be exactly 17 valid VIN characters (I, O, Q not allowed)": {
            cause: "This row's VIN isn't a valid 17-character VIN -- it may be the wrong length or contain a letter (I, O, or Q) that's never used in a real VIN.",
            fix: "Check the vin value for this row against the vehicle's registration.",
        },
        "web import does not accept a documents CSV; upload document files individually per driver after import": {
            cause: "This row is a document record, but the admin upload tool only imports driver profile data -- document files are uploaded per-driver afterwards.",
            fix: "Remove document rows from this CSV, import the drivers first, then upload each driver's documents individually from their driver page.",
        },
        "driver already imported by a previous run; no changes to apply": {
            cause: "This driver was already imported by an earlier run of this same tool, and nothing in this row has changed since.",
            fix: "This is expected on a re-run of the same file and is not a problem. No action needed.",
        },
        "date parses differently day-first vs month-first; verify the source sheet's format before commit": {
            cause: "This row's date could mean two different real dates depending on whether the source used day-first or month-first formatting (e.g. 03/04/2025 could be March 4 or April 3).",
            fix: "Check the original spreadsheet's date format and confirm this value means what you expect before committing.",
        },
    },
    [
        [
            "no vehicle_types row matched",
            {
                cause: "This row's vehicle_type value doesn't match any vehicle type Spinr has configured.",
                fix: "Check the vehicle_type value for this row against Spinr's configured vehicle types (Settings → Vehicle Types).",
            },
        ],
        [
            "driver already imported; updating",
            {
                cause: "This driver was already imported by an earlier run, but this row has different vehicle details -- the changed fields are being updated.",
                fix: "This is expected when re-uploading a corrected file (e.g. adding a VIN that was missing before) and is not a problem. No action needed.",
            },
        ],
    ],
);

// ── Bulk Rider Import ────────────────────────────────────────────────────
export const explainRiderImportIssue: IssueExplainer = createIssueExplainer(
    {
        "CSV is empty": {
            cause: "The uploaded file has no data rows.",
            fix: "Check that the file wasn't saved empty or truncated during export.",
        },
        "CSV is missing required column: phone": {
            cause: "The uploaded CSV has no phone column, which every row needs to be matched against Spinr.",
            fix: "Re-download the CSV template above and confirm your file's header row includes phone.",
        },
        "phone is required": {
            cause: "This row has no phone number, so there's nothing to match it to an account.",
            fix: "Fill in the phone number for this row, or remove the row.",
        },
        "duplicate phone in CSV": {
            cause: "The same phone number appears on more than one row in this file.",
            fix: "Keep only one row per phone number -- decide which is correct and remove the other.",
        },
        "phone matches existing DRIVER — will update stripe_customer_id if provided, rider flags already set": {
            cause: "This phone number already belongs to a Spinr driver -- they already have rider access, so this row will only update their Stripe customer ID if one was provided.",
            fix: "This is expected and not a problem -- drivers can also ride as riders. No action needed.",
        },
        "phone matches existing user — will update fields if provided": {
            cause: "This phone number already belongs to a Spinr account -- this row will update that account's fields rather than create a new one.",
            fix: "This is expected when re-uploading a CSV with updated details for an existing rider. No action needed.",
        },
    },
    [
        [
            "invalid phone format (expected +1XXXXXXXXXX):",
            {
                cause: "This row's phone number isn't in the expected +1 followed by 10 digits format.",
                fix: "Check the phone value for this row -- it may be missing the country code, have extra characters, or be the wrong length.",
            },
        ],
        [
            "customer_id '",
            {
                cause: "This row's customer_id doesn't start with cus_, which is how Stripe customer IDs are always formatted.",
                fix: "Check the customer_id value for this row -- it may be a different kind of ID, or blank/malformed.",
            },
        ],
        [
            "SKIPPED — matched account status is",
            {
                cause: "This row's phone matches an account that's in the middle of being deleted or already deleted -- Spinr never re-populates personal data onto an account like that automatically.",
                fix: "This needs manual review, not a re-run of the import -- check the matched account's status in the admin Users page and decide by hand whether this row should be imported at all.",
            },
        ],
    ],
);
