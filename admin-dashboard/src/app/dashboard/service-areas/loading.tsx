export default function ServiceAreasLoading() {
    return (
        <div aria-busy="true" aria-label="Loading service areas" className="animate-pulse">
            <div className="flex items-center justify-between mb-8">
                <div className="space-y-2">
                    <div className="h-7 w-40 rounded-lg bg-muted" />
                    <div className="h-4 w-72 rounded bg-muted" />
                </div>
                <div className="h-10 w-32 rounded-xl bg-muted" />
            </div>
            <div className="space-y-3">
                {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="bg-card rounded-2xl border p-5 flex items-center gap-4">
                        <div className="h-10 w-10 rounded-xl bg-muted shrink-0" />
                        <div className="flex-1 space-y-1.5">
                            <div className="h-4 w-40 rounded bg-muted" />
                            <div className="h-3 w-56 rounded bg-muted" />
                        </div>
                        <div className="h-3 w-24 rounded bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
