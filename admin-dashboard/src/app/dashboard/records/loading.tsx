export default function RecordsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading records and compliance" className="space-y-4 animate-pulse">
            <div className="h-7 w-56 rounded-lg bg-muted" />
            <div className="flex gap-1 bg-muted rounded-xl p-1 w-fit">
                {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="h-8 w-32 rounded-lg bg-card" />
                ))}
            </div>
            <div className="rounded-xl border border-border bg-card p-4 space-y-3">
                {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="h-4 rounded bg-muted" />
                ))}
            </div>
        </div>
    );
}
