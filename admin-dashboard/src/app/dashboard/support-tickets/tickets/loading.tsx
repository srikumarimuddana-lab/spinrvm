export default function SupportTicketsListLoading() {
    return (
        <div aria-busy="true" aria-label="Loading tickets" className="space-y-4 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="h-7 w-28 rounded-lg bg-muted" />
                <div className="h-9 w-32 rounded-lg bg-muted" />
            </div>
            <div className="flex flex-wrap items-center gap-2">
                <div className="h-9 w-48 rounded-lg bg-muted" />
                <div className="h-9 w-32 rounded-lg bg-muted" />
                <div className="h-9 w-32 rounded-lg bg-muted" />
                <div className="h-9 w-32 rounded-lg bg-muted" />
            </div>
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-3">
                        <div className="h-4 w-40 rounded bg-muted" />
                        <div className="h-5 w-16 rounded-full bg-muted" />
                        <div className="h-5 w-16 rounded-full bg-muted" />
                        <div className="h-4 w-24 rounded bg-muted" />
                        <div className="h-4 w-20 rounded bg-muted ml-auto" />
                    </div>
                ))}
            </div>
        </div>
    );
}
