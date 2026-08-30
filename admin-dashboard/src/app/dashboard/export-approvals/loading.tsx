export default function ExportApprovalsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading export approvals" className="space-y-4 animate-pulse">
            <div className="h-7 w-56 rounded-lg bg-muted" />
            {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="rounded-xl border border-border bg-card p-4 space-y-3">
                    <div className="flex items-center justify-between">
                        <div className="h-4 w-56 rounded bg-muted" />
                        <div className="h-5 w-20 rounded-full bg-muted" />
                    </div>
                    <div className="h-3 w-72 rounded bg-muted" />
                    <div className="flex justify-end gap-2">
                        <div className="h-9 w-20 rounded-lg bg-muted" />
                        <div className="h-9 w-20 rounded-lg bg-muted" />
                    </div>
                </div>
            ))}
        </div>
    );
}
