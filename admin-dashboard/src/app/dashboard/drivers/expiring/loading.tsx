export default function ExpiringLoading() {
    return (
        <div aria-busy="true" aria-label="Loading expiring documents" className="space-y-4 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="h-7 w-44 rounded-lg bg-muted" />
                <div className="h-9 w-28 rounded-lg bg-muted" />
            </div>
            <div className="flex gap-2">
                <div className="h-8 w-16 rounded-full bg-muted" />
                <div className="h-8 w-16 rounded-full bg-muted" />
                <div className="h-8 w-16 rounded-full bg-muted" />
            </div>
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-4">
                        <div className="h-10 w-10 rounded-full bg-muted shrink-0" />
                        <div className="flex-1 space-y-1.5">
                            <div className="h-3.5 w-32 rounded bg-muted" />
                            <div className="h-3 w-44 rounded bg-muted" />
                        </div>
                        <div className="h-5 w-20 rounded-full bg-muted" />
                        <div className="h-4 w-16 rounded bg-muted" />
                        <div className="h-8 w-20 rounded bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
