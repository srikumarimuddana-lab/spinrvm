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
