export default function DriversLoading() {
    return (
        <div aria-busy="true" aria-label="Loading drivers" className="space-y-4 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="h-7 w-36 rounded-lg bg-muted" />
                <div className="h-9 w-28 rounded-lg bg-muted" />
            </div>
            <div className="flex flex-wrap gap-2">
                <div className="h-9 w-44 rounded-lg bg-muted" />
                <div className="h-9 w-40 rounded-lg bg-muted" />
                <div className="h-9 w-36 rounded-lg bg-muted" />
                <div className="h-9 w-36 rounded-lg bg-muted" />
                <div className="h-9 flex-1 min-w-[200px] rounded-lg bg-muted" />
            </div>
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-4">
                        <div className="relative shrink-0">
                            <div className="h-10 w-10 rounded-full bg-muted" />
                        </div>
                        <div className="space-y-1.5 flex-1">
                            <div className="h-3.5 w-28 rounded bg-muted" />
                            <div className="h-3 w-40 rounded bg-muted" />
                        </div>
                        <div className="h-5 w-14 rounded-full bg-muted" />
                        <div className="h-4 w-12 rounded bg-muted" />
                        <div className="h-4 w-16 rounded bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
