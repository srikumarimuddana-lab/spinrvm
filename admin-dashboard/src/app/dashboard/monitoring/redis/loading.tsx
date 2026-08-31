export default function MonitoringRedisLoading() {
    return (
        <div aria-busy="true" aria-label="Loading Redis monitoring" className="space-y-4 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="h-7 w-48 rounded-lg bg-muted" />
                <div className="h-9 w-24 rounded-lg bg-muted" />
            </div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-2">
                        <div className="h-3 w-20 rounded bg-muted" />
                        <div className="h-7 w-16 rounded bg-muted" />
                    </div>
                ))}
            </div>
            <div className="rounded-xl border border-border bg-card p-4 space-y-3">
                {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="flex items-center justify-between">
                        <div className="h-4 w-40 rounded bg-muted" />
                        <div className="h-4 w-20 rounded bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
