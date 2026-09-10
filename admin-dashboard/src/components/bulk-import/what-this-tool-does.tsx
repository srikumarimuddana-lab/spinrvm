/**
 * Shared "what/why/which files/value" explainer panel for Bulk Import tools.
 *
 * Introduced with the Legacy Tax-ID Backfill UX pilot (2026-09-09) to answer
 * the recurring operator question this whole redesign exists for: what does
 * this tool do, why does it exist, which files does it need, and what's the
 * benefit. Extracted here so every tool in the rollout gets the same
 * consistent shape instead of a bespoke prose panel per page.
 */

import { HelpCircle } from "lucide-react";

export interface WhatThisToolDoesProps {
    what: React.ReactNode;
    why: React.ReactNode;
    whichFiles: React.ReactNode;
    value: React.ReactNode;
    /** Optional tool-specific safety guarantee (e.g. "never overwrites a value already on file"). */
    safetyNote?: React.ReactNode;
}

export function WhatThisToolDoes({ what, why, whichFiles, value, safetyNote }: WhatThisToolDoesProps) {
    return (
        <div className="space-y-3 rounded-md border border-muted bg-muted/30 p-4 text-sm">
            <div className="flex items-center gap-2 font-medium">
                <HelpCircle className="h-4 w-4" />
                What this tool does, in plain terms
            </div>
            <dl className="grid gap-3 sm:grid-cols-2">
                <div>
                    <dt className="text-xs font-semibold uppercase text-muted-foreground">What</dt>
                    <dd className="text-muted-foreground">{what}</dd>
                </div>
                <div>
                    <dt className="text-xs font-semibold uppercase text-muted-foreground">Why</dt>
                    <dd className="text-muted-foreground">{why}</dd>
                </div>
                <div>
                    <dt className="text-xs font-semibold uppercase text-muted-foreground">Which files</dt>
                    <dd className="text-muted-foreground">{whichFiles}</dd>
                </div>
                <div>
                    <dt className="text-xs font-semibold uppercase text-muted-foreground">Value</dt>
                    <dd className="text-muted-foreground">{value}</dd>
                </div>
            </dl>
            {safetyNote ? <p className="border-t pt-2 text-xs text-muted-foreground">{safetyNote}</p> : null}
        </div>
    );
}
