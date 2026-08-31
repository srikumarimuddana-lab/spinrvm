export default function VehicleTypesLoading() {
    return (
        <div aria-busy="true" aria-label="Loading vehicle types" className="space-y-6 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="space-y-2">
                    <div className="h-8 w-40 rounded-lg bg-muted" />
                    <div className="h-4 w-80 rounded bg-muted" />
                </div>
                <div className="h-9 w-40 rounded-lg bg-muted" />
            </div>
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="rounded-xl border border-border bg-card overflow-hidden">
                        <div className="h-32 bg-muted" />
                        <div className="p-4 space-y-3">
                            <div className="flex items-center justify-between">
                                <div className="h-5 w-24 rounded bg-muted" />
                                <div className="h-5 w-14 rounded-full bg-muted" />
                            </div>
                            <div className="h-3 w-full rounded bg-muted" />
                            <div className="h-3 w-2/3 rounded bg-muted" />
                        </div>
                    </div>
                ))}
            </div>
        </div>
    );
}
