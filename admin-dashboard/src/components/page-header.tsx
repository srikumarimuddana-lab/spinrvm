import { ReactNode } from "react";

/**
 * Shared top-of-page heading for dashboard pages — consolidates what were
 * hand-rolled `<h1 className="text-3xl font-bold tracking-tight">` blocks
 * copy-pasted across Rides, Users, Earnings, Settings, Corporate Accounts,
 * Vehicle Types, the dashboard home, Drivers, Service Areas, Staff, and
 * Analytics (and a few smaller variants elsewhere), each with slightly
 * different title/description/action markup.
 */
export interface PageHeaderProps {
    /** Page title. Usually a string, but accepts a node so a page can keep
     *  a small leading icon inline with the text (e.g. Users, Analytics). */
    title: ReactNode;
    /** Optional one-line description shown under the title. */
    description?: ReactNode;
    /** Right-aligned slot — action buttons ("+ New X"), filters, etc. */
    actions?: ReactNode;
    /** Overrides the header row's layout classes for a page that needs a
     *  different wrap/align behavior than the default (e.g. a responsive
     *  column layout on small screens). Defaults to a simple space-between
     *  row. */
    className?: string;
}

export function PageHeader({ title, description, actions, className }: PageHeaderProps) {
    return (
        <div className={className ?? "flex items-center justify-between"}>
            <div>
                <h1 className="text-3xl font-bold tracking-tight">{title}</h1>
                {description && <p className="text-muted-foreground mt-1">{description}</p>}
            </div>
            {actions}
        </div>
    );
}
