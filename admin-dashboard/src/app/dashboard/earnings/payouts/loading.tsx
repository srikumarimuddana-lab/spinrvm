export default function EarningsPayoutsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading payouts" className="space-y-4 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="h-7 w-32 rounded-lg bg-muted" />
                <div className="h-9 w-28 rounded-lg bg-muted" />
            </div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-2">
                        <div className="h-3 w-20 rounded bg-muted" />
                        <div className="h-7 w-24 rounded bg-muted" />
                    </div>
                ))}
            </div>
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 10 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-3">
                        <div className="space-y-1.5 flex-1">
                            <div className="h-3.5 w-32 rounded bg-muted" />
                            <div className="h-3 w-24 rounded bg-muted" />
                        </div>
                        <div className="h-4 w-16 rounded bg-muted" />
                        <div className="h-5 w-20 rounded-full bg-muted" />
                        <div className="h-4 w-20 rounded bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
