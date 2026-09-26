import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

/**
 * Shared "nothing here yet" block for dashboard lists (UX program W5.3).
 * Consolidates the hand-rolled centred icon + heading + hint markup that
 * list pages copy-pasted, each with slightly different icon sizes,
 * opacities, spacing and heading tags.
 *
 * The markup is the pattern those pages already shared most often (audit
 * logs, staff, promotions, cloud messaging), so adopting it there renders
 * the same DOM. The icon is decorative: lucide marks it `aria-hidden`.
 */
export interface EmptyStateProps {
    /** A lucide icon for the kind of item the list holds. */
    icon: LucideIcon;
    /** What's missing, e.g. "No staff members yet". */
    title: ReactNode;
    /** Optional hint on what to do next, e.g. "Try adjusting your filters." */
    description?: ReactNode;
    /** Heading level for the title, to fit the page's outline: 3 when the
     *  list sits under an `h2` section, or where a page already used `h3`. */
    headingLevel?: 2 | 3;
}

export function EmptyState({ icon: Icon, title, description, headingLevel = 2 }: EmptyStateProps) {
    const Heading = headingLevel === 3 ? "h3" : "h2";
    return (
        <div className="text-center py-16">
            <Icon className="h-12 w-12 text-muted-foreground/30 mx-auto mb-4" />
            <Heading className="text-lg font-semibold">{title}</Heading>
            {description != null && <p className="text-muted-foreground mt-1">{description}</p>}
        </div>
    );
}
