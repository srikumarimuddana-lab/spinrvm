export default function EarningsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading earnings and payouts" className="space-y-6 animate-pulse">
            <div className="h-8 w-64 rounded-lg bg-muted" />
            <div className="flex gap-1 bg-muted rounded-xl p-1 w-fit">
                {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} className="h-8 w-28 rounded-lg bg-card" />
                ))}
            </div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-2">
                        <div className="h-3 w-20 rounded bg-muted" />
                        <div className="h-7 w-28 rounded bg-muted" />
                    </div>
                ))}
            </div>
            <div className="rounded-xl border border-border bg-card p-4 h-64" />
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="h-4 mx-4 my-3 rounded bg-muted" />
                ))}
            </div>
        </div>
    );
}
