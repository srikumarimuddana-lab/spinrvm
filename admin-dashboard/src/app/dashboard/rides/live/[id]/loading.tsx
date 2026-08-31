export default function LiveRideTrackingLoading() {
    return (
        <div aria-busy="true" aria-label="Loading live ride tracking" className="h-[calc(100vh-80px)] flex flex-col animate-pulse">
            <div className="flex items-center gap-3 p-4 border-b bg-card">
                <div className="h-8 w-8 rounded-lg bg-muted" />
                <div className="flex-1 space-y-1.5">
                    <div className="h-5 w-32 rounded bg-muted" />
                    <div className="h-3 w-48 rounded bg-muted" />
                </div>
            </div>
            <div className="flex-1 flex flex-col lg:flex-row">
                <div className="flex-1 bg-muted" />
                <div className="w-full lg:w-80 border-t lg:border-t-0 lg:border-l bg-card p-4 space-y-4">
                    {Array.from({ length: 4 }).map((_, i) => (
                        <div key={i} className="rounded-xl bg-muted/40 p-3 space-y-2">
                            <div className="h-3 w-16 rounded bg-muted" />
                            <div className="h-4 w-40 rounded bg-muted" />
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}
