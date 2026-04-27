export default function MonitoringLoading() {
    return (
        <div aria-busy="true" aria-label="Loading monitoring" className="flex h-[calc(100vh-8rem)] gap-4 animate-pulse">
            <div className="w-72 shrink-0 rounded-xl border border-border bg-card space-y-2 p-3">
                {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-3 rounded-lg p-2">
                        <div className="h-9 w-9 rounded-full bg-muted shrink-0" />
                        <div className="space-y-1.5 flex-1">
                            <div className="h-3 w-24 rounded bg-muted" />
                            <div className="h-2.5 w-16 rounded bg-muted" />
                        </div>
                    </div>
                ))}
            </div>
            <div className="flex-1 rounded-xl border border-border bg-muted" />
        </div>
    );
}
