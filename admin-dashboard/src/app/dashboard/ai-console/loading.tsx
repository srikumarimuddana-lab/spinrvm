export default function AiConsoleLoading() {
    return (
        <div aria-busy="true" aria-label="Loading AI console" className="space-y-4 animate-pulse">
            <div className="space-y-1.5">
                <div className="h-6 w-32 rounded-lg bg-muted" />
                <div className="h-4 w-96 max-w-full rounded bg-muted" />
            </div>
            <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
                <div className="rounded-xl border border-border bg-card p-4 space-y-3">
                    <div className="h-4 w-16 rounded bg-muted" />
                    <div className="grid grid-cols-2 gap-2">
                        <div className="h-9 rounded-lg bg-muted" />
                        <div className="h-9 rounded-lg bg-muted" />
                    </div>
                    <div className="h-9 rounded-lg bg-muted" />
                    <div className="space-y-1.5 pt-2">
                        {Array.from({ length: 4 }).map((_, i) => (
                            <div key={i} className="h-4 rounded bg-muted" />
                        ))}
                    </div>
                </div>
                <div className="rounded-xl border border-border bg-card p-4 space-y-3 lg:col-span-2">
                    <div className="h-4 w-48 rounded bg-muted" />
                    <div className="h-[420px] rounded-lg bg-muted/60" />
                </div>
            </div>
        </div>
    );
}
