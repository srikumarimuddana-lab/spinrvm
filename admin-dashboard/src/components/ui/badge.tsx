import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"
import { Slot } from "radix-ui"

import { cn } from "@/lib/utils"

const badgeVariants = cva(
  "inline-flex items-center justify-center rounded-full border border-transparent px-2 py-0.5 text-xs font-medium w-fit whitespace-nowrap shrink-0 [&>svg]:size-3 gap-1 [&>svg]:pointer-events-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive transition-[color,box-shadow] overflow-hidden",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground [a&]:hover:bg-primary/90",
        secondary:
          "bg-secondary text-secondary-foreground [a&]:hover:bg-secondary/90",
        destructive:
          "bg-destructive text-white [a&]:hover:bg-destructive/90 focus-visible:ring-destructive/20 dark:focus-visible:ring-destructive/40 dark:bg-destructive/60",
        outline:
          "border-border text-foreground [a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
        // "Quiet Console" addition: the accent-bordered sibling of `outline`
        // above, for the one item in a same-weight group that's worth a
        // second look (e.g. the super_admin role among 5 roles) without
        // reaching for a filled, competing hue the way ROLE_COLORS-style
        // ad-hoc badges do today. Pure addition — no existing call site
        // uses this variant, so it has zero effect until adopted.
        "outline-accent":
          "border-primary text-primary [a&]:hover:bg-primary/10",
        // Three more quiet/outline siblings, reusing the app's EXISTING
        // semantic tokens (--success/--warning/--destructive — already
        // WCAG-verified, not new colors) rather than the ad-hoc bright
        // bg-{color}-100 fills scattered across ~24 files (#2816-adjacent).
        // Meant to replace those call sites: real semantic status (a
        // driver's document approved/pending/rejected, a ride
        // completed/cancelled) keeps a distinguishable color, just muted
        // and outlined instead of a filled pastel chip — the fix is fewer,
        // shared, quieter treatments, not stripping status color entirely.
        "outline-success":
          "border-success text-success [a&]:hover:bg-success/10",
        "outline-warning":
          "border-warning text-warning [a&]:hover:bg-warning/10",
        "outline-destructive":
          "border-destructive text-destructive [a&]:hover:bg-destructive/10",
        // Closes a gap Stage 3 hit for real: some states (a mid-severity
        // safety tier, an "open" ticket) are neither positive nor negative
        // nor purely categorical — they need a distinct "in-progress/
        // informational" tone the other four can't honestly cover without
        // misreading as success/warning/danger. Reuses --info, the
        // existing WCAG-verified blue token (already used elsewhere for
        // the same "pending/processing" meaning) — not a new color.
        "outline-info":
          "border-info text-info [a&]:hover:bg-info/10",
        ghost: "[a&]:hover:bg-accent [a&]:hover:text-accent-foreground",
        link: "text-primary underline-offset-4 [a&]:hover:underline",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
)

function Badge({
  className,
  variant = "default",
  asChild = false,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "span"

  return (
    <Comp
      data-slot="badge"
      data-variant={variant}
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
