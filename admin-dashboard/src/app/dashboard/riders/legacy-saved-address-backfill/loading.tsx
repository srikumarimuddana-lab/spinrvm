export default function SavedAddressBackfillLoading() {
    return (
        <div aria-busy="true" aria-label="Loading saved-address backfill" className="mx-auto max-w-4xl space-y-6 p-4 animate-pulse">
            <div className="space-y-2">
                <div className="h-7 w-96 rounded-lg bg-muted" />
                <div className="h-4 w-full rounded bg-muted" />
                <div className="h-4 w-2/3 rounded bg-muted" />
            </div>
            <div className="h-16 rounded-md bg-muted" />
            <div className="rounded-xl border border-border bg-card p-6 space-y-4">
                <div className="h-5 w-64 rounded bg-muted" />
                {Array.from({ length: 2 }).map((_, i) => (
                    <div key={i} className="space-y-1.5">
                        <div className="h-3.5 w-32 rounded bg-muted" />
                        <div className="h-9 rounded-lg bg-muted" />
                    </div>
                ))}
                <div className="h-9 w-28 rounded-lg bg-muted" />
            </div>
        </div>
    );
}
