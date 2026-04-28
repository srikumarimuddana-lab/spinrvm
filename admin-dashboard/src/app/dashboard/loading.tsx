export default function DashboardLoading() {
    return (
        <div aria-busy="true" aria-label="Loading dashboard" className="space-y-6 animate-pulse">
            <div className="h-8 w-48 rounded-lg bg-muted" />
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-2">
                        <div className="h-3 w-20 rounded bg-muted" />
                        <div className="h-7 w-28 rounded bg-muted" />
                    </div>
                ))}
            </div>
            <div className="rounded-xl border border-border bg-card p-4 space-y-3">
                {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="h-4 rounded bg-muted" />
                ))}
            </div>
        </div>
    );
}
