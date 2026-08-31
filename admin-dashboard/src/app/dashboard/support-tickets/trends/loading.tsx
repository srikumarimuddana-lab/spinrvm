export default function SupportTicketsTrendsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading ticket trends" className="space-y-6 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="h-7 w-28 rounded-lg bg-muted" />
                <div className="flex items-center gap-2">
                    <div className="h-9 w-32 rounded-lg bg-muted" />
                    <div className="h-9 w-32 rounded-lg bg-muted" />
                </div>
            </div>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
                {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-3">
                        <div className="h-5 w-32 rounded bg-muted" />
                        <div className="h-48 rounded-lg bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
