export default function DocumentRequirementsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading document requirements" className="space-y-6 p-6 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <div className="h-5 w-5 rounded bg-muted" />
                    <div className="h-6 w-6 rounded bg-muted" />
                    <div className="space-y-1.5">
                        <div className="h-6 w-56 rounded-lg bg-muted" />
                        <div className="h-3.5 w-72 rounded bg-muted" />
                    </div>
                </div>
                <div className="flex gap-2">
                    <div className="h-9 w-24 rounded-lg bg-muted" />
                    <div className="h-9 w-36 rounded-lg bg-muted" />
                </div>
            </div>
            <div className="grid grid-cols-3 gap-4">
                {Array.from({ length: 3 }).map((_, i) => (
                    <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-2">
                        <div className="h-3 w-28 rounded bg-muted" />
                        <div className="h-7 w-10 rounded bg-muted" />
                    </div>
                ))}
            </div>
            {Array.from({ length: 2 }).map((_, i) => (
                <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-3">
                    <div className="h-4 w-40 rounded bg-muted" />
                    {Array.from({ length: 3 }).map((_, j) => (
                        <div key={j} className="flex items-center gap-4">
                            <div className="h-3.5 w-32 rounded bg-muted" />
                            <div className="h-3.5 w-24 rounded bg-muted" />
                            <div className="h-5 w-16 rounded-full bg-muted" />
                            <div className="h-3.5 flex-1 rounded bg-muted" />
                        </div>
                    ))}
                </div>
            ))}
        </div>
    );
}
