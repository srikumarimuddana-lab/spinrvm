export default function DriverLicenseBackfillLoading() {
    return (
        <div aria-busy="true" aria-label="Loading driver license backfill" className="space-y-4 animate-pulse">
            <div className="h-7 w-64 rounded-lg bg-muted" />
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-3">
                        <div className="space-y-1.5 flex-1">
                            <div className="h-3.5 w-32 rounded bg-muted" />
                            <div className="h-3 w-48 rounded bg-muted" />
                        </div>
                        <div className="h-9 w-32 rounded-lg bg-muted" />
                        <div className="h-9 w-24 rounded-lg bg-muted" />
                        <div className="h-8 w-20 rounded bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
