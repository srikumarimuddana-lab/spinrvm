export default function VenuesLoading() {
    return (
        <div aria-busy="true" aria-label="Loading pickup venues" className="space-y-6 animate-pulse">
            <div className="flex items-start justify-between gap-4 flex-wrap">
                <div className="space-y-2">
                    <div className="h-7 w-44 rounded-lg bg-muted" />
                    <div className="h-4 w-96 max-w-full rounded bg-muted" />
                </div>
                <div className="flex items-center gap-2">
                    <div className="h-9 w-24 rounded-lg bg-muted" />
                    <div className="h-9 w-28 rounded-lg bg-muted" />
                </div>
            </div>
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 7 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-3">
                        <div className="h-4 w-32 rounded bg-muted" />
                        <div className="h-5 w-16 rounded-full bg-muted" />
                        <div className="h-4 w-28 rounded bg-muted" />
                        <div className="h-4 w-14 rounded bg-muted" />
                        <div className="h-4 w-10 rounded bg-muted" />
                        <div className="h-4 w-16 rounded bg-muted ml-auto" />
                    </div>
                ))}
            </div>
        </div>
    );
}
