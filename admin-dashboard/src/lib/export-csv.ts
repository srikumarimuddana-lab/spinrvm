/**
 * Export an array of objects to a CSV file and trigger download.
 */
export function exportToCsv(filename: string, rows: Record<string, any>[], columns?: { key: string; label: string }[]) {
    if (rows.length === 0) return;

    // Determine columns: use provided or auto-detect from keys
    const cols = columns || Object.keys(rows[0]).map((k) => ({ key: k, label: k }));

    // Header row
    const header = cols.map((c) => `"${c.label}"`).join(",");

    // Data rows
    const csvRows = rows.map((row) =>
        cols
            .map((c) => {
                let val = row[c.key];
                if (val === null || val === undefined) val = "";
                if (typeof val === "object") val = JSON.stringify(val);
                // Escape quotes
                val = String(val).replace(/"/g, '""');
                return `"${val}"`;
            })
            .join(",")
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
