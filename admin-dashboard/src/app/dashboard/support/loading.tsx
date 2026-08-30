export default function SupportLoading() {
    return (
        <div aria-busy="true" aria-label="Loading support and issues" className="px-1 sm:px-0 space-y-4 animate-pulse">
            <div className="mb-4 space-y-1.5">
                <div className="h-6 w-56 rounded-lg bg-muted" />
                <div className="h-4 w-72 rounded bg-muted" />
            </div>
            <div className="flex gap-4 border-b pb-2 overflow-x-auto">
                {Array.from({ length: 7 }).map((_, i) => (
                    <div key={i} className="h-6 w-28 shrink-0 rounded bg-muted" />
                ))}
            </div>
            <div className="rounded-xl border border-border bg-card overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-3">
                        <div className="h-4 w-48 rounded bg-muted" />
                        <div className="h-4 w-20 rounded bg-muted" />
                        <div className="h-4 w-16 rounded bg-muted ml-auto" />
                    </div>
                ))}
            </div>
        </div>
    );
}
