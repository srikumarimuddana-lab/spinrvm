export default function CorporateAccountsPolicyLoading() {
    return (
        <div aria-busy="true" aria-label="Loading ride policy" className="space-y-6 p-6 max-w-2xl animate-pulse">
            <div className="flex items-center gap-3">
                <div className="h-8 w-24 rounded-lg bg-muted" />
                <div className="h-7 w-32 rounded-lg bg-muted" />
                <div className="h-5 w-20 rounded-full bg-muted" />
            </div>
            <div className="h-4 w-full rounded bg-muted" />
            {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-2">
                    <div className="h-4 w-40 rounded bg-muted" />
                    <div className="h-3.5 w-64 max-w-full rounded bg-muted" />
                    <div className="h-9 w-48 rounded-lg bg-muted" />
                </div>
            ))}
        </div>
    );
}
