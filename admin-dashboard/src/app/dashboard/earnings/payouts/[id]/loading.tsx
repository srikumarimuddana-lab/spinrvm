export default function EarningsPayoutDetailLoading() {
    return (
        <div aria-busy="true" aria-label="Loading payout details" className="space-y-4 animate-pulse">
            <div className="flex items-center gap-3">
                <div className="h-9 w-9 rounded-lg bg-muted" />
                <div className="h-7 w-48 rounded-lg bg-muted" />
            </div>
            <div className="rounded-xl border border-border bg-card p-5 space-y-4">
                <div className="flex items-center justify-between">
                    <div className="h-5 w-32 rounded bg-muted" />
                    <div className="h-6 w-24 rounded-full bg-muted" />
                </div>
                <div className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                    {Array.from({ length: 6 }).map((_, i) => (
                        <div key={i} className="space-y-1.5">
                            <div className="h-3 w-20 rounded bg-muted" />
                            <div className="h-4 w-28 rounded bg-muted" />
                        </div>
                    ))}
                </div>
            </div>
            <div className="rounded-xl border border-border bg-card p-5 space-y-3">
                <div className="h-5 w-24 rounded bg-muted" />
                {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="h-4 rounded bg-muted" />
                ))}
            </div>
        </div>
    );
}
