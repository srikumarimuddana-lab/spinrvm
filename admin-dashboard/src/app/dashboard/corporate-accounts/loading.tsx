export default function CorporateAccountsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading corporate accounts" className="space-y-6 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="space-y-1.5">
                    <div className="h-8 w-56 rounded-lg bg-muted" />
                    <div className="h-4 w-64 rounded bg-muted" />
                </div>
                <div className="flex gap-2">
                    <div className="h-9 w-28 rounded-lg bg-muted" />
                    <div className="h-9 w-9 rounded-lg bg-muted" />
                    <div className="h-9 w-32 rounded-lg bg-muted" />
                </div>
            </div>
            <div className="flex items-center gap-2">
                <div className="h-9 flex-1 max-w-sm rounded-lg bg-muted" />
                <div className="h-9 w-52 rounded-lg bg-muted" />
            </div>
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-3">
                        <div className="space-y-1.5 flex-1">
                            <div className="h-3.5 w-40 rounded bg-muted" />
                            <div className="h-3 w-24 rounded bg-muted" />
                        </div>
                        <div className="h-5 w-20 rounded-full bg-muted" />
                        <div className="h-3.5 w-16 rounded bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
