export default function HelpDeskLoading() {
    return (
        <div aria-busy="true" aria-label="Loading help desk" className="space-y-6 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="space-y-2">
                    <div className="h-7 w-36 rounded-lg bg-muted" />
                    <div className="h-4 w-48 rounded bg-muted" />
                </div>
                <div className="flex items-center gap-2">
                    <div className="h-9 w-32 rounded-lg bg-muted" />
                    <div className="h-9 w-24 rounded-lg bg-muted" />
                    <div className="h-9 w-24 rounded-lg bg-muted" />
                </div>
            </div>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
                {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-2">
                        <div className="h-3 w-20 rounded bg-muted" />
                        <div className="h-7 w-16 rounded bg-muted" />
                    </div>
                ))}
            </div>
            <div className="rounded-xl border border-border bg-card p-4 space-y-3">
                <div className="h-5 w-40 rounded bg-muted" />
                {Array.from({ length: 5 }).map((_, i) => (
                    <div key={i} className="h-12 rounded-lg bg-muted" />
                ))}
            </div>
        </div>
    );
}
