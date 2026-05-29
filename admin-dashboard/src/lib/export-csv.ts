/**
 * Export an array of objects to a CSV file and trigger download.
 *
 * Columns may specify either a `key` (simple property lookup) or a `value`
 * function that receives the row and returns the cell value. `value`
 * wins when both are supplied; this lets callers flatten JSONB columns
 * (e.g. rides.phase_distances.trip_in_progress) without mutating the
 * source data.
 */
export type CsvColumn = {
    key?: string;
    label: string;
    value?: (row: Record<string, any>) => unknown;
};

/**
 * Neutralize spreadsheet formula injection (OWASP CSV Injection prevention).
 * Excel / Sheets / LibreOffice treat cells starting with =, +, -, @, tab, or
 * CR as formulas. Prefix with a literal apostrophe to force text interpretation.
 */
export function sanitizeCsvCell(val: string): string {
    if (val.length > 0 && /^[=+\-@\t\r]/.test(val)) {
        return `'${val}`;
    }
    return val;
}

export function exportToCsv(
    filename: string,
    rows: Record<string, any>[],
    columns?: CsvColumn[],
) {
    if (rows.length === 0) return;

    const cols: CsvColumn[] =
        columns || Object.keys(rows[0]).map((k) => ({ key: k, label: k }));

    const header = cols.map((c) => `"${c.label}"`).join(",");

    const csvRows = rows.map((row) =>
        cols
            .map((c) => {
                let val = c.value ? c.value(row) : c.key ? row[c.key] : "";
                if (val === null || val === undefined) val = "";
                if (typeof val === "object") val = JSON.stringify(val);
                val = sanitizeCsvCell(String(val));
                val = val.replace(/"/g, '""');
                return `"${val}"`;
            })
            .join(","),
    );

    const csv = [header, ...csvRows].join("\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${filename}_${new Date().toISOString().split("T")[0]}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
}
