export default function CorporateAccountsDetailLoading() {
    return (
        <div aria-busy="true" aria-label="Loading corporate account" className="space-y-6 animate-pulse">
            <div className="flex items-start justify-between gap-4">
                <div className="space-y-1.5">
                    <div className="h-4 w-40 rounded bg-muted" />
                    <div className="h-8 w-64 rounded-lg bg-muted" />
                </div>
                <div className="flex flex-wrap gap-2">
                    <div className="h-9 w-24 rounded-lg bg-muted" />
                    <div className="h-9 w-20 rounded-lg bg-muted" />
                    <div className="h-9 w-28 rounded-lg bg-muted" />
                </div>
            </div>
            {Array.from({ length: 2 }).map((_, i) => (
                <div key={i} className="rounded-xl border border-border bg-card p-6 space-y-4">
                    <div className="h-5 w-40 rounded bg-muted" />
                    <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
                        {Array.from({ length: 6 }).map((_, j) => (
                            <div key={j} className="space-y-1.5">
                                <div className="h-3 w-24 rounded bg-muted" />
                                <div className="h-4 w-32 rounded bg-muted" />
                            </div>
                        ))}
                    </div>
                </div>
            ))}
        </div>
    );
}
