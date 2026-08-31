export default function CloudMessagingLoading() {
    return (
        <div aria-busy="true" aria-label="Loading notifications" className="space-y-6 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="space-y-1.5">
                    <div className="h-8 w-48 rounded-lg bg-muted" />
                    <div className="h-4 w-96 max-w-full rounded bg-muted" />
                </div>
                <div className="h-9 w-24 rounded-lg bg-muted" />
            </div>
            <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
                {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-2">
                        <div className="h-3 w-16 rounded bg-muted" />
                        <div className="h-6 w-12 rounded bg-muted" />
                    </div>
                ))}
            </div>
            <div className="flex gap-4 border-b border-border pb-2">
                <div className="h-5 w-32 rounded bg-muted" />
                <div className="h-5 w-24 rounded bg-muted" />
                <div className="h-5 w-32 rounded bg-muted" />
            </div>
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-3">
                        <div className="space-y-1.5 flex-1">
                            <div className="h-3.5 w-40 rounded bg-muted" />
                            <div className="h-3 w-56 rounded bg-muted" />
                        </div>
                        <div className="h-5 w-16 rounded-full bg-muted" />
                        <div className="h-3.5 w-20 rounded bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
