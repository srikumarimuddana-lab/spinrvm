export default function CorporateAccountsMembersLoading() {
    return (
        <div aria-busy="true" aria-label="Loading corporate account members" className="space-y-6 p-6 animate-pulse">
            <div className="flex items-center gap-3">
                <div className="h-8 w-24 rounded-lg bg-muted" />
                <div className="h-7 w-28 rounded-lg bg-muted" />
                <div className="h-8 w-32 rounded-lg bg-muted" />
            </div>
            <div className="rounded-xl border border-border bg-card p-4">
                <div className="flex flex-wrap items-end gap-2">
                    <div className="h-9 w-72 rounded-lg bg-muted" />
                    <div className="h-9 w-36 rounded-lg bg-muted" />
                    <div className="h-9 w-32 rounded-lg bg-muted" />
                </div>
            </div>
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="flex gap-4 border-b border-border px-4 py-3">
                    <div className="h-4 w-12 rounded bg-muted" />
                    <div className="h-4 w-16 rounded bg-muted" />
                    <div className="h-4 w-20 rounded bg-muted" />
                </div>
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-3">
                        <div className="h-3.5 w-40 rounded bg-muted" />
                        <div className="h-3.5 w-16 rounded bg-muted" />
                        <div className="h-5 w-20 rounded-full bg-muted" />
                        <div className="h-3.5 flex-1 rounded bg-muted" />
                    </div>
                ))}
            </div>
        </div>
    );
}
