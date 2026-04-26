export default function AnalyticsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading analytics" className="space-y-6 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="h-7 w-40 rounded-lg bg-muted" />
                <div className="h-9 w-36 rounded-lg bg-muted" />
            </div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-2">
                        <div className="h-3 w-20 rounded bg-muted" />
                        <div className="h-7 w-24 rounded bg-muted" />
                    </div>
                ))}
            </div>
            <div className="rounded-xl border border-border bg-card p-4">
                <div className="h-5 w-32 rounded bg-muted mb-4" />
                <div className="h-48 rounded-lg bg-muted" />
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                {Array.from({ length: 2 }).map((_, i) => (
                    <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-3">
                        <div className="h-5 w-28 rounded bg-muted" />
                        {Array.from({ length: 5 }).map((_, j) => (
                            <div key={j} className="flex justify-between">
                                <div className="h-4 w-24 rounded bg-muted" />
                                <div className="h-4 w-16 rounded bg-muted" />
                            </div>
                        ))}
                    </div>
                ))}
            </div>
        </div>
    );
}
