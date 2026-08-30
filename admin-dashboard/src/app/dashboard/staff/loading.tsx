export default function StaffLoading() {
    return (
        <div aria-busy="true" aria-label="Loading staff management" className="animate-pulse">
            <div className="flex items-center justify-between mb-8">
                <div className="space-y-2">
                    <div className="h-7 w-52 rounded-lg bg-muted" />
                    <div className="h-4 w-72 rounded bg-muted" />
                </div>
                <div className="h-9 w-28 rounded-lg bg-muted" />
            </div>
            <div className="rounded-xl border border-border bg-card overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="flex items-center gap-4 border-t border-border px-4 py-4">
                        <div className="h-4 w-32 rounded bg-muted" />
                        <div className="h-4 w-44 rounded bg-muted" />
                        <div className="h-5 w-20 rounded-full bg-muted" />
                        <div className="h-4 w-28 rounded bg-muted" />
                        <div className="h-5 w-16 rounded-full bg-muted" />
                        <div className="h-4 w-20 rounded bg-muted ml-auto" />
                    </div>
                ))}
            </div>
        </div>
    );
}
