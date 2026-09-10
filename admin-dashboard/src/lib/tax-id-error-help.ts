/**
 * Plain-language explanations for the Legacy Tax-ID Backfill's validation
 * messages (backend/routes/admin/tax_id_import.py + backend/utils/sin.py).
 *
 * The backend intentionally returns short, technical strings here (never a
 * SIN/GST/phone value, per PIPEDA) -- this file exists so an operator
 * doesn't have to bring an error CSV to Claude/support to understand what a
 * message means and what to do about it. Matching is by prefix/substring
 * against the exact strings those two files emit; if either changes a
 * message, add the new string here rather than editing the backend to match
 * this file.
 */

export interface IssueExplanation {
    /** One line: what actually happened, in plain language. */
    cause: string;
    /** One line: what to do about it. */
    fix: string;
}

const EXACT: Record<string, IssueExplanation> = {
    "CSV header must be exactly: phone,sin,gst_bn": {
        cause: "The file's first row (the column names) doesn't match what this tool expects.",
        fix: 'Open the CSV and make sure row 1 is exactly "phone,sin,gst_bn" (lowercase, no extra spaces or columns), or use the "Prepare from Mongo export" option below to build it automatically.',
    },
    "CSV has no data rows": {
        cause: "The file has a header row but no actual data underneath it.",
        fix: "Check that the CSV wasn't saved empty, and that it wasn't accidentally truncated on export.",
    },
    "phone is required": {
        cause: "This row has no phone number, so there's nothing to match it to a driver.",
        fix: "Fill in the phone number for this row, or delete the row if it has no driver to match.",
    },
    "duplicate phone in CSV": {
        cause: "The same phone number appears on more than one row in this file.",
        fix: "Keep only one row per phone number — the most recent one. If this came from a Mongo export, use the \"Prepare from Mongo export\" option below, which automatically keeps the latest record per driver.",
    },
    "no driver with this phone": {
        cause: "No driver in Spinr has this phone number on file, so this row can't be matched.",
        fix: "Double check the phone number is correct and that this driver has already completed sign-up in the app. If the number looks right, this driver may not exist yet in Spinr.",
    },
    "row has neither sin nor gst_bn": {
        cause: "This row has no SIN and no GST/HST business number — there's nothing for this tool to write.",
        fix: "Fill in at least one of the two values, or remove this row.",
    },
    "not a valid BN (9 digits, optional RTxxxx)": {
        cause: "The GST/HST business number isn't in the expected format (9 digits, optionally followed by RT and 4 more digits, e.g. 123456789RT0001).",
        fix: "Check this value against the driver's GST/HST registration — it's likely missing digits, has extra characters, or the RT suffix is malformed.",
    },
    "SIN is required": {
        cause: "This row has a SIN column but it's empty.",
        fix: "Fill in the SIN, or leave both SIN and the GST column blank if this driver's SIN isn't being backfilled.",
    },
    "SIN cannot start with 0": {
        cause: "Canadian SINs never start with the digit 0 — this value can't be a real SIN.",
        fix: "Double-check this value against the source record; it's likely a typo or a non-SIN value ended up in this column.",
    },
    "SIN cannot be a single repeated digit": {
        cause: "This value is 9 copies of the same digit (e.g. 111111111), which is never a real SIN.",
        fix: "This is almost certainly a placeholder or data-entry error in the source file — check the original record.",
    },
    "SIN failed its checksum — check for a mistyped digit": {
        cause: "This 9-digit value doesn't pass the standard SIN validity check, which usually means one digit was mistyped.",
        fix: "Compare it against the original document (SIN card / previous app record) for a transposed or mistyped digit.",
    },
    "SIN already on file — skipped (use update-sin to correct)": {
        cause: "This driver already has a SIN on file, so this tool left it alone — SIN values can't be overwritten by a bulk import.",
        fix: "This is expected and not a problem. If the SIN on file is actually wrong, correct it individually from the driver's profile (Update SIN), which keeps an audit trail.",
    },
    "GST BN already on file — skipped": {
        cause: "This driver already has a GST/HST business number on file, so this tool left it alone.",
        fix: "This is expected and not a problem — it prevents a bulk import from silently overwriting a number the driver already corrected in the app.",
    },
    "nothing to write — both values already on file": {
        cause: "Both the SIN and GST/HST number for this row are already on file for this driver.",
        fix: "No action needed — this row is already up to date.",
    },
    "SIN set since validation — skipped": {
        cause: "Between validating and committing, this driver added their own SIN in the app — so this tool correctly backed off rather than overwrite it.",
        fix: "No action needed. If you still believe the CSV value should apply, verify with the driver first, then use Update SIN individually.",
    },
    "GST BN set since validation — skipped": {
        cause: "Between validating and committing, this driver's GST/HST number was set some other way — so this tool correctly backed off rather than overwrite it.",
        fix: "No action needed — re-validate if you want to see the current state.",
    },
    "No rows with a SIN or GST/HST BN matched a driver by phone": {
        cause: "After joining banks.csv and drivers.csv, not a single row both had a SIN/GST value and matched an existing driver's phone number.",
        fix: "Double-check you uploaded the correct pair of files, and that these drivers have already completed sign-up in Spinr.",
    },
};

// Prefix matches for messages that carry a dynamic value (a count, a digit
// total) that EXACT can't key on.
const PREFIXES: [string, IssueExplanation][] = [
    [
        "CSV has ",
        {
            cause: "This file has more rows than this tool accepts in a single upload.",
            fix: "Split the file into smaller batches and upload them one at a time.",
        },
    ],
    [
        "SIN must be 9 digits",
        {
            cause: "A valid Canadian SIN is always exactly 9 digits — this value has too few or too many.",
            fix: "Check this value against the source record for missing or extra digits.",
        },
    ],
    [
        "CSV exceeds the",
        {
            cause: "The uploaded file is larger than this tool accepts.",
            fix: "Split the export into smaller files and upload them separately.",
        },
    ],
    [
        "CSV must be UTF-8 encoded",
        {
            cause: "The file's text encoding isn't UTF-8, so it can't be read reliably.",
            fix: "Re-save or re-export the CSV with UTF-8 encoding (most spreadsheet tools offer this as a save option).",
        },
    ],
];

/** Looks up a plain-language explanation for a backend validation message.
 * Returns null when no mapping exists (still-technical, but honest, rather
 * than guessing at an explanation). */
export function explainTaxIdIssue(message: string): IssueExplanation | null {
    if (EXACT[message]) return EXACT[message];
    for (const [prefix, explanation] of PREFIXES) {
        if (message.startsWith(prefix)) return explanation;
    }
    return null;
}
