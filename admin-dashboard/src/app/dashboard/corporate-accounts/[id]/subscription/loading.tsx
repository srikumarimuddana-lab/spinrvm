export default function CorporateAccountsSubscriptionLoading() {
    return (
        <div aria-busy="true" aria-label="Loading subscription billing" className="space-y-6 p-6 max-w-2xl animate-pulse">
            <div className="flex items-center gap-3">
                <div className="h-8 w-20 rounded-lg bg-muted" />
                <div className="h-7 w-48 rounded-lg bg-muted" />
                <div className="h-8 w-8 rounded-lg bg-muted" />
            </div>
            <div className="h-4 w-full rounded bg-muted" />
            <div className="rounded-xl border border-border bg-card p-4 space-y-3">
                <div className="flex items-center justify-between">
                    <div className="space-y-1.5">
                        <div className="h-4 w-32 rounded bg-muted" />
                        <div className="h-3.5 w-24 rounded bg-muted" />
                    </div>
                    <div className="h-5 w-20 rounded-full bg-muted" />
                </div>
                <div className="h-3.5 w-56 rounded bg-muted" />
                <div className="flex items-center gap-3 pt-2 border-t border-border">
                    <div className="h-5 w-9 rounded-full bg-muted" />
                    <div className="h-3.5 w-52 rounded bg-muted" />
                    <div className="h-9 w-36 ml-auto rounded-lg bg-muted" />
                </div>
            </div>
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-9 bg-muted/60" />
                {Array.from({ length: 3 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-3">
                        <div className="h-3.5 flex-1 rounded bg-muted" />
                        <div className="h-3.5 w-16 rounded bg-muted" />
                        <div className="h-5 w-16 rounded-full bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
