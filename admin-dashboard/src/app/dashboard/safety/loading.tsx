export default function SafetyLoading() {
    return (
        <div aria-busy="true" aria-label="Loading safety queue" className="space-y-4 animate-pulse">
            <div className="flex items-start justify-between gap-3 flex-wrap">
                <div className="space-y-1.5">
                    <div className="h-6 w-40 rounded-lg bg-muted" />
                    <div className="h-4 w-72 rounded bg-muted" />
                </div>
                <div className="h-8 w-28 rounded-lg bg-muted" />
            </div>
            <div className="flex items-center gap-2 flex-wrap">
                <div className="h-8 w-[240px] rounded-lg bg-muted" />
                <div className="h-8 w-[170px] rounded-lg bg-muted" />
                <div className="h-8 w-[180px] rounded-lg bg-muted" />
                <div className="h-8 w-[140px] rounded-lg bg-muted" />
            </div>
            <div className="rounded-xl border border-border overflow-hidden bg-card">
                <div className="h-9 bg-muted/60" />
                {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-3">
                        <div className="h-4 w-20 rounded bg-muted" />
                        <div className="h-4 w-16 rounded bg-muted" />
                        <div className="h-4 w-24 rounded bg-muted" />
                        <div className="h-4 w-28 rounded bg-muted" />
                        <div className="h-4 w-20 rounded bg-muted" />
                        <div className="h-5 w-16 rounded-full bg-muted" />
                        <div className="h-4 w-16 rounded bg-muted ml-auto" />
                    </div>
                ))}
            </div>
        </div>
    );
}
