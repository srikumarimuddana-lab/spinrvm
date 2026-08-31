export default function StripeEventsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading Stripe webhook events" className="space-y-6 p-6 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="space-y-2">
                    <div className="h-7 w-64 rounded-lg bg-muted" />
                    <div className="h-4 w-80 rounded bg-muted" />
                </div>
                <div className="flex items-center gap-2">
                    <div className="h-8 w-36 rounded-lg bg-muted" />
                    <div className="h-8 w-24 rounded-lg bg-muted" />
                </div>
            </div>
            <div className="rounded-xl border border-border bg-card p-4 space-y-3">
                <div className="h-5 w-32 rounded bg-muted" />
                {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border pt-3">
                        <div className="h-4 w-28 rounded bg-muted" />
                        <div className="h-4 w-20 rounded bg-muted" />
                        <div className="h-4 w-24 rounded bg-muted" />
                        <div className="h-4 w-16 rounded bg-muted" />
                        <div className="h-4 w-16 rounded bg-muted ml-auto" />
                    </div>
                ))}
            </div>
        </div>
    );
}
