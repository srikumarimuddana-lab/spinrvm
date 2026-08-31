"use client";

/**
 * Migration Checklist — live status panel for every legacy-migration/
 * bulk-import/backfill tool on this page (and its two sibling pages under
 * /dashboard/drivers/ and /dashboard/riders/), in the verified dependency
 * order documented in docs/runbooks/migration-tool-order.md.
 *
 * Read-only: one GET on mount (+ a manual refresh button), no writes of its
 * own. Every actual import/backfill is still run through its own dedicated
 * tool below — this panel only tells you what's already run and what to run
 * next, so a fresh Mongo export can be worked through top-to-bottom without
 * guessing at order.
 */

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { CheckCircle2, Circle, AlertTriangle, HelpCircle, RefreshCw, Loader2 } from "lucide-react";
import { adminGetMigrationStatus, type MigrationToolStatus, type MigrationToolState } from "@/lib/api";
import { Card, CardHeader, CardTitle, CardDescription, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";

const STATE_META: Record<MigrationToolState, { label: string; icon: typeof CheckCircle2; cls: string }> = {
    done: { label: "Done", icon: CheckCircle2, cls: "text-success" },
    partial: { label: "Partial", icon: AlertTriangle, cls: "text-warning" },
    not_started: { label: "Not started", icon: Circle, cls: "text-muted-foreground" },
    manual_check_required: { label: "Check manually", icon: HelpCircle, cls: "text-muted-foreground" },
};

function StateBadge({ state }: { state: MigrationToolState }) {
    const meta = STATE_META[state];
    const Icon = meta.icon;
    return (
        <span className={`inline-flex items-center gap-1.5 text-xs font-medium ${meta.cls}`}>
            <Icon className="h-3.5 w-3.5" />
            {meta.label}
        </span>
    );
}

function ChecklistRow({ tool }: { tool: MigrationToolStatus }) {
    return (
        <div className="flex items-start justify-between gap-4 border-b py-2.5 last:border-b-0">
            <div className="flex items-start gap-3">
                <span className="mt-0.5 w-5 shrink-0 text-right text-xs text-muted-foreground">
                    {tool.order}.
                </span>
                <div>
                    <Link href={tool.admin_path} className="text-sm font-medium underline-offset-2 hover:underline">
                        {tool.name}
                    </Link>
                    <p className="text-xs text-muted-foreground">{tool.detail}</p>
                </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
                {tool.warning && (
                    <span className="rounded-full bg-warning/15 px-2 py-0.5 text-xs font-medium text-warning">
                        {tool.warning}
                    </span>
                )}
                <StateBadge state={tool.state} />
            </div>
        </div>
    );
}

export function MigrationChecklist() {
    const [tools, setTools] = useState<MigrationToolStatus[] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        setLoading(true);
        setError(null);
        try {
            const res = await adminGetMigrationStatus();
            setTools(res.tools);
        } catch (e) {
            setError(e instanceof Error ? e.message : "Could not load migration status");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void load();
    }, [load]);

    return (
        <Card>
            <CardHeader className="flex flex-row items-start justify-between gap-4">
                <div>
                    <CardTitle>Migration Checklist</CardTitle>
                    <CardDescription>
                        All 16 legacy-migration tools, in dependency order — run top to bottom against a
                        fresh Mongo export. Each tool below is still its own dry-run-first process; this
                        panel only shows what&apos;s already run.
                    </CardDescription>
                </div>
                <Button variant="outline" size="sm" onClick={() => void load()} disabled={loading}>
                    {loading ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                    ) : (
                        <RefreshCw className="mr-2 h-4 w-4" />
                    )}
                    Refresh
                </Button>
            </CardHeader>
            <CardContent>
                {error && (
                    <p className="mb-3 text-sm text-destructive">{error}</p>
                )}
                {!tools && loading && (
                    <p className="text-sm text-muted-foreground">Loading status…</p>
                )}
                {tools && (
                    <div className="divide-y-0">
                        {tools.map((t) => (
                            <ChecklistRow key={t.id} tool={t} />
                        ))}
                    </div>
                )}
            </CardContent>
        </Card>
    );
}
