export default function DriverDecalsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading welcome letters" className="space-y-4 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="h-7 w-48 rounded-lg bg-muted" />
                <div className="h-9 w-28 rounded-lg bg-muted" />
            </div>
            <div className="flex flex-wrap gap-2">
                {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="h-9 w-28 rounded-lg bg-muted" />
                ))}
            </div>
            <div className="flex gap-2">
                <div className="h-9 flex-1 rounded-lg bg-muted" />
                <div className="h-9 w-40 rounded-lg bg-muted" />
                <div className="h-9 w-40 rounded-lg bg-muted" />
            </div>
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-3">
                        <div className="h-9 w-9 rounded-full bg-muted shrink-0" />
                        <div className="space-y-1.5 flex-1">
                            <div className="h-3.5 w-32 rounded bg-muted" />
                            <div className="h-3 w-48 rounded bg-muted" />
                        </div>
                        <div className="h-5 w-20 rounded-full bg-muted" />
                        <div className="h-8 w-24 rounded bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
