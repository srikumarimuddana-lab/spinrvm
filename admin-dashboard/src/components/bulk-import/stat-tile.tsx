/** Shared stat tile for Bulk Import tools' review-and-commit summaries. */

export interface StatTileProps {
    label: string;
    value: number;
    tone?: "warn" | "error";
}

export function StatTile({ label, value, tone }: StatTileProps) {
    const toneCls =
        tone === "error" && value > 0
            ? "text-destructive"
            : tone === "warn" && value > 0
              ? "text-warning"
              : "text-foreground";
    return (
        <div className="rounded-md border p-3">
            <div className={`text-2xl font-semibold ${toneCls}`}>{value}</div>
            <div className="text-xs text-muted-foreground">{label}</div>
        </div>
    );
}
