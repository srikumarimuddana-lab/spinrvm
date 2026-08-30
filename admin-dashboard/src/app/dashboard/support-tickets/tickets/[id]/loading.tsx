export default function SupportTicketDetailLoading() {
    return (
        <div aria-busy="true" aria-label="Loading ticket detail" className="space-y-4 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="h-8 w-40 rounded-lg bg-muted" />
                <div className="h-8 w-36 rounded-lg bg-muted" />
            </div>
            <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
                <div className="lg:col-span-2 space-y-4">
                    <div className="rounded-xl border border-border bg-card p-4 space-y-2">
                        <div className="h-5 w-64 rounded bg-muted" />
                        <div className="h-3 w-40 rounded bg-muted" />
                    </div>
                    <div className="rounded-xl border border-border bg-card p-4 space-y-3">
                        <div className="h-4 w-28 rounded bg-muted" />
                        {Array.from({ length: 3 }).map((_, i) => (
                            <div key={i} className="h-16 rounded-lg bg-muted" />
                        ))}
                    </div>
                    <div className="rounded-xl border border-border bg-card p-4 space-y-2">
                        <div className="h-4 w-32 rounded bg-muted" />
                        <div className="h-20 rounded-lg bg-muted" />
                    </div>
                </div>
                <div className="space-y-4">
                    {Array.from({ length: 3 }).map((_, i) => (
                        <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-2">
                            <div className="h-4 w-24 rounded bg-muted" />
                            <div className="h-3 w-full rounded bg-muted" />
                            <div className="h-3 w-2/3 rounded bg-muted" />
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
