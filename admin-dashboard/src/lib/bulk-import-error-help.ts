/**
 * Shared factory for Bulk Import tools' plain-language error/warning
 * explanations, generalized from lib/tax-id-error-help.ts (the Legacy Tax-ID
 * Backfill pilot). Each tool's backend emits its own vocabulary of short,
 * technical messages -- this factory builds a lookup (exact match, then
 * prefix match for messages carrying a dynamic suffix, then suffix match for
 * messages carrying a dynamic prefix) so each tool exports a small,
 * tool-specific map instead of duplicating the lookup logic.
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
 * (for messages carrying a dynamic suffix, e.g. "invalid SIN, skipped: <reason>")
 * and optional suffix rules (for messages carrying a dynamic prefix, e.g.
 * "acct_123 is already mapped to another driver"). */
export function createIssueExplainer(
    exact: Record<string, IssueExplanation>,
    prefixes: [string, IssueExplanation][] = [],
    suffixes: [string, IssueExplanation][] = [],
): IssueExplainer {
    return (message: string) => {
        if (exact[message]) return exact[message];
        for (const [prefix, explanation] of prefixes) {
            if (message.startsWith(prefix)) return explanation;
        }
        for (const [suffix, explanation] of suffixes) {
            if (message.endsWith(suffix)) return explanation;
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

// ── Legacy Stripe Mapping Import ────────────────────────────────────────
export const explainStripeMappingIssue: IssueExplainer = createIssueExplainer(
    {
        // -- local matching/guard phase (drivers) --
        "stripe_account_id must look like acct_...": {
            cause: "This row's stripe_account_id doesn't look like a Stripe Connect account ID (Stripe account IDs always start with acct_).",
            fix: "Check the stripe_account_id value for this row -- it may be blank, truncated, or a different kind of ID.",
        },
        "row has neither old_driver_id nor phone to match on": {
            cause: "This row has nothing to match it to an existing Spinr driver -- both old_driver_id and phone are blank.",
            fix: "Fill in at least one of old_driver_id or phone for this row, or remove it.",
        },
        "old_driver_id and phone resolve to different drivers": {
            cause: "This row's old_driver_id matches one Spinr driver, but its phone number matches a different one -- the tool can't tell which is correct.",
            fix: "Check this row's old_driver_id and phone against the admin Drivers page and correct whichever one is wrong.",
        },
        "no driver with this phone/old_driver_id found": {
            cause: "Neither this row's old_driver_id nor its phone number matches any driver already in Spinr.",
            fix: "This driver may not have gone through Legacy Driver Import yet -- re-run this tool after that import, or check the values for a typo.",
        },
        "multiple CSV rows resolve to the same record": {
            cause: "More than one row in this file matches the same Spinr driver or rider -- only one row can update that record.",
            fix: "Check the file for duplicate rows referencing the same driver/rider and keep only the correct one.",
        },
        // -- local matching/guard phase (riders) --
        "stripe_customer_id must look like cus_...": {
            cause: "This row's stripe_customer_id doesn't look like a Stripe Customer ID (Stripe customer IDs always start with cus_).",
            fix: "Check the stripe_customer_id value for this row -- it may be blank, truncated, or a different kind of ID.",
        },
        "row has neither phone nor email to match on": {
            cause: "This row has nothing to match it to an existing Spinr rider -- both phone and email are blank.",
            fix: "Fill in at least one of phone or email for this row, or remove it.",
        },
        "phone and email resolve to different users": {
            cause: "This row's phone number matches one Spinr account, but its email matches a different one -- the tool can't tell which is correct.",
            fix: "Check this row's phone and email against the admin Users page and correct whichever one is wrong.",
        },
        "no user matches this row": {
            cause: "Neither this row's phone number nor its email matches any account already in Spinr.",
            fix: "This rider may not have gone through Bulk Rider Import yet -- re-run this tool after that import, or check the values for a typo.",
        },
        "user already has a different stripe_customer_id; drop this row": {
            cause: "This rider already has a Stripe customer ID in Spinr (likely created automatically the first time they paid in the new app) -- it's different from the one in this CSV row.",
            fix: "This is expected for riders who already used the new app before this import ran -- drop this row and have the rider re-add their card if needed. No action needed otherwise.",
        },
        // -- warnings: already-mapped / ignored old-id --
        "driver already carries this stripe_account_id; skipped": {
            cause: "This driver already has this exact Stripe account ID saved in Spinr -- there's nothing to update.",
            fix: "This is expected on a re-run of the same file and is not a problem. No action needed.",
        },
        "old_stripe_account_id ignored (not acct_...)": {
            cause: "This row's old_stripe_account_id column has a value, but it doesn't look like a real Stripe account ID, so it was ignored rather than saved.",
            fix: "This is non-blocking -- the current stripe_account_id is still applied. Check the old_stripe_account_id value only if you need that history preserved.",
        },
        "user already carries this stripe_customer_id; skipped": {
            cause: "This rider already has this exact Stripe customer ID saved in Spinr -- there's nothing to update.",
            fix: "This is expected on a re-run of the same file and is not a problem. No action needed.",
        },
        "old_stripe_customer_id ignored (not cus_...)": {
            cause: "This row's old_stripe_customer_id column has a value, but it doesn't look like a real Stripe customer ID, so it was ignored rather than saved.",
            fix: "This is non-blocking -- the current stripe_customer_id is still applied. Check the old_stripe_customer_id value only if you need that history preserved.",
        },
        // -- live Stripe validation phase --
        "stripe_secret_key is not set in app settings": {
            cause: "Spinr's Stripe secret key isn't configured, so no row in this batch can be checked against real Stripe data.",
            fix: "Set stripe_secret_key in Settings → App Settings, then re-run validate.",
        },
        "Stripe error while validating this row; re-run validate": {
            cause: "Stripe returned a temporary error while this row was being checked -- not a problem with the row's data itself.",
            fix: "Re-run validate. If it keeps failing on the same row, check Stripe's status page for an ongoing incident.",
        },
        "transfers capability was never requested on this account": {
            cause: "This Stripe Connect account was never set up to receive payouts (transfers) -- it may have been created for a different purpose or incompletely.",
            fix: "Check this account directly in the Stripe Dashboard before mapping it as a driver's payout destination.",
        },
        "details_submitted/payouts_enabled not yet true; driver finishes via in-app Stripe onboarding": {
            cause: "This driver's Stripe account exists but onboarding isn't fully complete yet (missing bank details, identity verification, or similar).",
            fix: "This is expected and non-blocking -- the mapping is still applied, and the driver finishes the remaining steps through the app's existing Stripe onboarding flow. No action needed.",
        },
        "customer is deleted in Stripe": {
            cause: "This Stripe customer ID belongs to a customer that's been deleted in Stripe -- it can no longer hold a payment method.",
            fix: "Drop this row; the rider will need a new Stripe customer created (e.g. by adding a card in the app), not this legacy ID.",
        },
        "Stripe customer metadata.user_id differs from the matched user": {
            cause: "This Stripe customer object already has a different Spinr user_id recorded in its own Stripe metadata than the account this row matched to.",
            fix: "This is non-blocking, but double-check in the Stripe Dashboard that this customer really belongs to the rider this row matched -- a mismatch here can mean the row matched the wrong account.",
        },
    },
    [
        // -- dynamic value as a MESSAGE PREFIX (fixed part after it) --
        [
            "account country is ",
            {
                cause: "This driver's Stripe Connect account isn't registered in Canada -- Spinr requires a CA-country account to pay out Canadian drivers.",
                fix: "Check this account in the Stripe Dashboard; it may be from the wrong Stripe platform account or a different country's onboarding flow.",
            },
        ],
        [
            "transfers capability is ",
            {
                cause: "This Stripe Connect account's payout (transfers) capability isn't fully active yet -- it's pending, restricted, or inactive.",
                fix: "This is non-blocking; the driver finishes onboarding through the app's existing Stripe flow. Check the account in the Stripe Dashboard if it stays incomplete for a long time.",
            },
        ],
        [
            "account is disabled (",
            {
                cause: "Stripe has disabled this Connect account -- either it was rejected during review or the platform paused it.",
                fix: "Check the account's disabled_reason in the Stripe Dashboard before deciding whether this driver can be mapped at all.",
            },
        ],
        [
            "account type is ",
            {
                cause: "This Stripe Connect account isn't an Express account -- Spinr's driver payout flow is built around Express accounts specifically.",
                fix: "This is non-blocking, but double-check in the Stripe Dashboard that this account is really the right one for this driver.",
            },
        ],
        [
            "business_type is ",
            {
                cause: "This Stripe Connect account is registered as a business, not an individual -- most Spinr drivers onboard as individuals.",
                fix: "This is non-blocking, but double-check in the Stripe Dashboard that this account is really the right one for this driver.",
            },
        ],
        [
            "outstanding requirements: ",
            {
                cause: "Stripe still needs more information from this account before it can be fully verified (the specific fields follow the colon in the message above).",
                fix: "This is non-blocking -- the driver finishes these through the app's existing Stripe onboarding flow. No action needed unless it stays incomplete for a long time.",
            },
        ],
        [
            "object is ",
            {
                cause: "The Stripe object this row points to is in test mode while Spinr's configured Stripe key is live mode (or vice versa) -- the two can never be mapped together.",
                fix: "Confirm which Stripe platform account and mode (test/live) this CSV's IDs actually came from before re-running.",
            },
        ],
    ],
    [
        // -- dynamic value as a MESSAGE PREFIX with a fixed SUFFIX --
        [
            "appears on multiple CSV rows",
            {
                cause: "The same Stripe ID appears on more than one row in this file -- it can't be mapped to more than one driver or rider.",
                fix: "Check the file for duplicate Stripe IDs and keep only the correct row for each one.",
            },
        ],
        [
            "is already mapped to another driver",
            {
                cause: "This Stripe account ID is already saved on a different Spinr driver's record -- assigning it here would move the same payout destination to two drivers.",
                fix: "Check the admin Drivers page for which driver already has this Stripe account ID, and confirm this row's driver match is correct before proceeding.",
            },
        ],
        [
            "is already mapped to another user",
            {
                cause: "This Stripe customer ID is already saved on a different Spinr rider's account -- assigning it here would move the same saved card to two riders.",
                fix: "Check the admin Users page for which rider already has this Stripe customer ID, and confirm this row's rider match is correct before proceeding.",
            },
        ],
        [
            "does not exist on this platform or is not accessible",
            {
                cause: "Stripe couldn't find this ID at all under Spinr's configured Stripe key -- it may belong to a different Stripe platform account, a different mode (test vs. live), or be mistyped.",
                fix: "Confirm this CSV's Stripe IDs really came from the same Stripe platform account Spinr is configured with (see the migration-scenario note above the upload form).",
            },
        ],
    ],
);

// ── Imported Ride Snapshots / Routes (Route Map Snapshots + Route Backfill) ─
// Both tools operate on the same imported-ride population and share two of
// their four possible per-ride error strings ("missing coordinates",
// "db update: <exception>") -- see routes/admin/rides.py's
// admin_regenerate_imported_snapshots / admin_regenerate_imported_routes.
// Unlike every other tool in this file, "upload: <exc>" / "db update: <exc>"
// interpolate the raw underlying exception text, which has no fixed
// vocabulary -- these two get a generic, honest explanation rather than a
// guess at what the specific exception could be.
export const explainRouteRegenIssue: IssueExplainer = createIssueExplainer(
    {
        "missing coordinates": {
            cause: "This ride is missing a pickup or dropoff latitude/longitude, so there's nothing to draw a route between.",
            fix: "Check this ride's pickup_lat/pickup_lng/dropoff_lat/dropoff_lng in the admin Rides page -- a legacy import row with blank coordinates can't get a snapshot or route until that's corrected.",
        },
        "render returned None": {
            cause: "Both the Google Static Maps renderer and the OSM fallback renderer failed to produce an image for this ride, for reasons logged server-side but not returned here.",
            fix: "Re-run Regenerate -- this is often a transient rendering/network issue. If it keeps failing on the same ride, check the backend logs for this ride_id.",
        },
        "no route from OSRM or Google Directions": {
            cause: "Neither OSRM nor the Google Directions fallback could compute a road route between this ride's pickup and dropoff points.",
            fix: "Check this ride's coordinates for a real Saskatchewan-area location -- an OSRM/Directions failure on both is more likely from a bad coordinate pair than a slow-passing map region.",
        },
    },
    [
        [
            "upload: ",
            {
                cause: "The rendered map image failed to upload to storage -- the specific reason follows the colon in the message above, straight from the storage error.",
                fix: "Re-run Regenerate -- this is usually transient. If it keeps failing on the same ride, check the backend logs for this ride_id and the storage error text shown above.",
            },
        ],
        [
            "db update: ",
            {
                cause: "The snapshot/route was generated successfully, but saving it to this ride's record failed -- the specific reason follows the colon in the message above, straight from the database error.",
                fix: "Re-run Regenerate -- this is usually transient. If it keeps failing on the same ride, check the backend logs for this ride_id and the database error text shown above.",
            },
        ],
    ],
);
