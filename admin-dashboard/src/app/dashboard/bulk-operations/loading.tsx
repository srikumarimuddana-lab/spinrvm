export default function BulkOperationsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading bulk operations" className="mx-auto max-w-4xl space-y-6 p-4 animate-pulse">
            <div className="space-y-1.5">
                <div className="h-7 w-44 rounded-lg bg-muted" />
                <div className="h-4 w-full rounded bg-muted" />
                <div className="h-4 w-3/4 rounded bg-muted" />
            </div>
            {Array.from({ length: 2 }).map((_, i) => (
                <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-4">
                    <div className="space-y-1.5">
                        <div className="h-5 w-56 rounded bg-muted" />
                        <div className="h-3.5 w-40 rounded bg-muted" />
                    </div>
                    <div className="grid gap-4 sm:grid-cols-2">
                        <div className="space-y-1.5">
                            <div className="h-3.5 w-32 rounded bg-muted" />
                            <div className="h-9 rounded-lg bg-muted" />
                        </div>
                        <div className="h-9 w-48 self-end rounded-lg bg-muted" />
                    </div>
                    <div className="h-9 w-40 rounded-lg bg-muted" />
                </div>
            ))}
        </div>
    );
}
