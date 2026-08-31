export default function HeatmapLoading() {
    return (
        <div aria-busy="true" aria-label="Loading demand heatmap" className="space-y-4 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="h-7 w-40 rounded-lg bg-muted" />
                <div className="flex gap-2">
                    <div className="h-9 w-32 rounded-lg bg-muted" />
                    <div className="h-9 w-24 rounded-lg bg-muted" />
                </div>
            </div>
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
                {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-2">
                        <div className="h-3 w-20 rounded bg-muted" />
                        <div className="h-7 w-16 rounded bg-muted" />
                    </div>
                ))}
            </div>
            <div className="rounded-xl border border-border bg-muted h-[600px]" />
        </div>
    );
}
