export default function DataTransferLoading() {
    return (
        <div aria-busy="true" aria-label="Loading data transfer" className="p-6 space-y-6 animate-pulse">
            <div className="space-y-1.5">
                <div className="h-7 w-40 rounded-lg bg-muted" />
                <div className="h-4 w-full rounded bg-muted" />
            </div>
            <div className="flex gap-4 border-b border-border pb-2">
                {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} className="h-5 w-24 rounded bg-muted" />
                ))}
            </div>
            <div className="rounded-xl border border-border bg-card p-4 space-y-3">
                <div className="h-5 w-40 rounded bg-muted" />
                <div className="h-3.5 w-full rounded bg-muted" />
                <div className="h-3.5 w-2/3 rounded bg-muted" />
                <div className="h-9 w-full rounded-lg bg-muted" />
            </div>
        </div>
    );
}
