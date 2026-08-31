export default function AuditLogsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading audit logs" className="space-y-6 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="space-y-1.5">
                    <div className="h-8 w-40 rounded-lg bg-muted" />
                    <div className="h-4 w-72 max-w-full rounded bg-muted" />
                </div>
                <div className="flex gap-2">
                    <div className="h-9 w-24 rounded-lg bg-muted" />
                    <div className="h-9 w-32 rounded-lg bg-muted" />
                </div>
            </div>
            <div className="flex flex-wrap items-center gap-3">
                <div className="h-9 flex-1 max-w-sm rounded-lg bg-muted" />
                <div className="h-9 w-48 rounded-lg bg-muted" />
                <div className="h-9 w-40 rounded-lg bg-muted" />
            </div>
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 10 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-3">
                        <div className="h-3.5 w-24 rounded bg-muted" />
                        <div className="h-3.5 w-32 rounded bg-muted" />
                        <div className="h-5 w-24 rounded-full bg-muted" />
                        <div className="h-3.5 w-20 rounded bg-muted" />
                        <div className="h-3.5 flex-1 rounded bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
