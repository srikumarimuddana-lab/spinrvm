export default function CorporateAccountsKybQueueLoading() {
    return (
        <div aria-busy="true" aria-label="Loading KYB queue" className="space-y-6 animate-pulse">
            <div className="h-8 w-56 rounded-lg bg-muted" />
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 8 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-3">
                        <div className="h-3.5 w-32 rounded bg-muted" />
                        <div className="h-3.5 w-24 rounded bg-muted" />
                        <div className="h-3.5 w-16 rounded bg-muted" />
                        <div className="h-3.5 w-20 rounded bg-muted" />
                        <div className="h-5 w-20 rounded-full bg-muted" />
                        <div className="h-3.5 flex-1 rounded bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
