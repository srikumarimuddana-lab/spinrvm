export default function SettingsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading settings" className="space-y-6 animate-pulse max-w-2xl">
            <div className="h-7 w-32 rounded-lg bg-muted" />
            {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="rounded-xl border border-border bg-card p-6 space-y-4">
                    <div className="h-5 w-40 rounded bg-muted" />
                    <div className="space-y-3">
                        {Array.from({ length: 3 }).map((_, j) => (
                            <div key={j} className="space-y-1.5">
                                <div className="h-3.5 w-24 rounded bg-muted" />
                                <div className="h-9 rounded-lg bg-muted" />
                            </div>
                        ))}
                    </div>
                </div>
            ))}
        </div>
    );
}
