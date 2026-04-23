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
                val = String(val).replace(/"/g, '""');
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
