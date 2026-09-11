export default function MonitoringDispatchGeoLoading() {
    return (
        <div aria-busy="true" aria-label="Loading dispatch geo-provider status" className="space-y-4 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="h-7 w-64 rounded-lg bg-muted" />
                <div className="h-9 w-40 rounded-lg bg-muted" />
            </div>
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                {Array.from({ length: 2 }).map((_, i) => (
                    <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-2">
                        <div className="h-4 w-32 rounded bg-muted" />
                        <div className="h-7 w-24 rounded bg-muted" />
                    </div>
                ))}
            </div>
            <div className="rounded-xl border border-border bg-card p-4 space-y-3">
                {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} className="flex items-center justify-between">
                        <div className="h-4 w-40 rounded bg-muted" />
                        <div className="h-4 w-20 rounded bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
