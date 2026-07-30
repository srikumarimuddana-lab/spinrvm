"use client";

/**
 * Small "i" hover-hint icon — explains a field, value, or piece of
 * functionality without needing a full docs page open alongside the
 * admin portal. Optionally links out to an authoritative external
 * reference (e.g. the CRA or SGI's own published guidance) when the
 * hint is explaining a regulatory/compliance concept rather than just
 * this app's own UI.
 *
 * Consolidates what were 3+ copy-pasted local `Hint` components
 * (compliance/page.tsx, data-transfer/ExportTab.tsx, SgiFormsTab.tsx) —
 * one shared component so future hints don't get a 4th slightly-different
 * copy, and so the hover-hint pattern can be rolled out to more of the
 * admin portal from a single place.
 */

import { HelpCircle, ExternalLink } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

export interface InfoHintProps {
    /** The hint text itself — keep it to 1-2 sentences; this is a hover
     *  aside, not a place for a paragraph. */
    text: string;
    /** Optional "Learn more" link — only pass this when there's a real,
     *  authoritative destination (official regulator guidance, this
     *  repo's own docs), never a placeholder or guessed URL. */
    href?: string;
    /** Visible label for the link, e.g. "CRA: Reportable Platform
     *  Operators". Defaults to "Learn more" if omitted. */
    linkLabel?: string;
    className?: string;
}

export function InfoHint({ text, href, linkLabel, className }: InfoHintProps) {
    return (
        <Tooltip>
            <TooltipTrigger asChild>
                <HelpCircle
                    className={className ?? "h-3.5 w-3.5 shrink-0 cursor-help text-muted-foreground"}
                    aria-label="More info"
                />
            </TooltipTrigger>
            <TooltipContent className="max-w-[280px] space-y-1">
                <p>{text}</p>
                {href && (
                    <a
                        href={href}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="inline-flex items-center gap-1 text-primary underline underline-offset-2"
                    >
                        {linkLabel || "Learn more"}
                        <ExternalLink className="h-3 w-3" />
                    </a>
                )}
            </TooltipContent>
        </Tooltip>
    );
}
