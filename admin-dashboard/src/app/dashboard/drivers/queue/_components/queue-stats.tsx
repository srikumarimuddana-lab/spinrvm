import { Clock, AlertTriangle, TrendingUp, Users } from "lucide-react";
import type { ApprovalQueueResponse } from "@/lib/api";

interface QueueStatsProps {
    stats: ApprovalQueueResponse["stats"];
}

const formatHours = (h: number) => {
    if (!h) return "0h";
    if (h < 1) return `${Math.round(h * 60)}m`;
    if (h < 24) return `${h.toFixed(1)}h`;
    return `${Math.floor(h / 24)}d ${Math.round(h % 24)}h`;
};

export function QueueStats({ stats }: QueueStatsProps) {
    const cards = [
        {
            label: "Pending",
            value: String(stats.total_pending),
            icon: Users,
            tone: "blue" as const,
        },
        {
            label: "Median wait",
            value: formatHours(stats.median_wait_hours),
            icon: Clock,
            tone: stats.median_wait_hours >= 24 ? ("red" as const)
                : stats.median_wait_hours >= 4 ? ("amber" as const)
                : ("emerald" as const),
        },
        {
            label: "Oldest in queue",
            value: formatHours(stats.oldest_in_queue_hours),
            icon: TrendingUp,
            tone: stats.oldest_in_queue_hours >= 24 ? ("red" as const)
                : stats.oldest_in_queue_hours >= 4 ? ("amber" as const)
                : ("emerald" as const),
        },
        {
            label: "Over 24h",
            value: String(stats.over_24h_count),
            icon: AlertTriangle,
            tone: stats.over_24h_count > 0 ? ("red" as const) : ("emerald" as const),
        },
    ];

    return (
        <div className="grid grid-cols-2 lg:grid-cols-4 gap-3">
            {cards.map((c) => {
                const Icon = c.icon;
                const toneClass = {
                    // "blue" has no semantic-token equivalent (only success/warning/destructive
                    // exist) — kept as a hand-picked neutral-informational color for the
                    // always-present "Pending" count, which isn't itself a good/bad signal. (#2816)
                    // eslint-disable-next-line no-restricted-syntax -- see comment above (#2816)
                    blue: "bg-blue-50 dark:bg-blue-900/20 text-blue-700 dark:text-blue-300",
                    emerald: "bg-success/15 text-success",
                    amber: "bg-warning/15 text-warning",
                    red: "bg-destructive/15 text-destructive",
                }[c.tone];
                return (
                    <div key={c.label} className="rounded-xl border border-border bg-card p-4">
                        <div className="flex items-start justify-between gap-3">
                            <div>
                                <p className="text-[11px] uppercase tracking-wider font-semibold text-muted-foreground">{c.label}</p>
                                <p className="mt-1 text-2xl font-bold tracking-tight">{c.value}</p>
                            </div>
                            <div className={`h-9 w-9 rounded-lg flex items-center justify-center ${toneClass}`}>
                                <Icon className="h-4 w-4" />
                            </div>
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
