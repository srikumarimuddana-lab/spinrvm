export default function LegacySinDobBackfillLoading() {
    return (
        <div aria-busy="true" aria-label="Loading legacy SIN/DOB backfill" className="space-y-4 animate-pulse">
            <div className="h-7 w-72 rounded-lg bg-muted" />
            <div className="rounded-xl border border-border bg-card p-5 space-y-4">
                <div className="h-4 w-80 rounded bg-muted" />
                {Array.from({ length: 2 }).map((_, i) => (
                    <div key={i} className="space-y-1.5">
                        <div className="h-3 w-24 rounded bg-muted" />
                        <div className="h-9 w-full rounded-lg bg-muted" />
                    </div>
                ))}
                <div className="h-9 w-40 rounded-lg bg-muted" />
            </div>
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-3">
                        <div className="h-4 w-40 rounded bg-muted" />
                        <div className="h-4 flex-1 rounded bg-muted" />
                        <div className="h-5 w-16 rounded-full bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
