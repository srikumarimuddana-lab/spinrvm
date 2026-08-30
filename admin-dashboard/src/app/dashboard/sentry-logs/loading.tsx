export default function SentryLogsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading Sentry issues" className="flex flex-col gap-4 p-6 animate-pulse">
            <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="space-y-1.5">
                    <div className="h-7 w-44 rounded-lg bg-muted" />
                    <div className="h-4 w-96 max-w-full rounded bg-muted" />
                </div>
                <div className="h-9 w-24 rounded-lg bg-muted" />
            </div>
            <div className="rounded-xl border border-border bg-card p-4 flex flex-wrap items-center gap-3">
                <div className="h-9 w-[160px] rounded-lg bg-muted" />
                <div className="h-9 w-[150px] rounded-lg bg-muted" />
                <div className="h-9 w-[150px] rounded-lg bg-muted" />
                <div className="h-4 w-20 rounded bg-muted ml-auto" />
            </div>
            {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-2">
                    <div className="h-4 w-2/3 rounded bg-muted" />
                    <div className="h-3 w-1/3 rounded bg-muted" />
                </div>
            ))}
        </div>
    );
}
