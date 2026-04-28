export default function FaqsLoading() {
    return (
        <div aria-busy="true" aria-label="Loading FAQs" className="space-y-4 animate-pulse">
            <div className="flex items-center justify-between">
                <div className="h-7 w-24 rounded-lg bg-muted" />
                <div className="h-9 w-28 rounded-lg bg-muted" />
            </div>
            <div className="flex gap-2">
                <div className="h-9 flex-1 rounded-lg bg-muted" />
                <div className="h-9 w-32 rounded-lg bg-muted" />
            </div>
            <div className="rounded-xl border border-border overflow-hidden">
                <div className="h-11 bg-muted/60" />
                {Array.from({ length: 6 }).map((_, i) => (
                    <div key={i} className="flex gap-4 border-t border-border px-4 py-3">
                        <div className="h-4 w-48 rounded bg-muted" />
                        <div className="h-4 w-20 rounded bg-muted" />
                        <div className="h-4 w-16 rounded bg-muted ml-auto" />
                    </div>
                ))}
            </div>
        </div>
    );
}
