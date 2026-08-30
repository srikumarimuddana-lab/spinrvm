export default function SubscriptionsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading Spinr Pass subscriptions" className="space-y-6 p-6 animate-pulse">
            {/* Header */}
            <div className="flex items-center justify-between">
                <div className="flex items-center gap-3">
                    <div className="h-6 w-6 rounded bg-muted" />
                    <div className="space-y-2">
                        <div className="h-6 w-64 rounded-lg bg-muted" />
                        <div className="h-4 w-80 rounded bg-muted" />
                    </div>
                </div>
                <div className="h-9 w-24 rounded-lg bg-muted" />
            </div>

            {/* Stats — 6 tiles */}
            <div className="grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-6">
                {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-2">
                        <div className="h-3 w-16 rounded bg-muted" />
                        <div className="h-7 w-20 rounded bg-muted" />
                    </div>
                ))}
            </div>

            {/* Tab bar — Plans / Driver Subscriptions / Transactions / Tax Config */}
            <div className="flex gap-4 border-b pb-2">
                {Array.from({ length: 4 }).map((_, i) => (
                    <div key={i} className="h-5 w-32 rounded bg-muted" />
                ))}
            </div>

            {/* Active tab: table */}
            <div className="rounded-xl border border-border bg-card overflow-hidden">
                <div className="flex items-center justify-between p-4 border-b border-border">
                    <div className="h-5 w-40 rounded bg-muted" />
                    <div className="h-8 w-28 rounded-lg bg-muted" />
                </div>
                <div className="h-10 bg-muted/60" />
                {Array.from({ length: 7 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-4">
                        <div className="h-4 w-32 rounded bg-muted" />
                        <div className="h-4 w-16 rounded bg-muted" />
                        <div className="h-4 w-16 rounded bg-muted" />
                        <div className="h-4 w-16 rounded bg-muted" />
                        <div className="h-4 w-16 rounded bg-muted" />
                        <div className="h-5 w-16 rounded-full bg-muted" />
                        <div className="h-4 w-16 rounded bg-muted ml-auto" />
                    </div>
                ))}
            </div>
        </div>
    );
}
