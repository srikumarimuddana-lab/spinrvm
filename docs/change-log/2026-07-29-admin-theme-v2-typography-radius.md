# Change Impact & Risk Log

## Summary

| Field | Value |
|---|---|
| Date | 2026-07-29 |
| Author | Claude Code |
| Surface(s) | admin-dashboard |
| Domain (Sentry tag) | admin |
| PR / commit link | (this PR) |
| Related issue or gap ID | #2785 (admin-dashboard visual-refresh epic), Phase 3 |

## 1. Issue / gap identified

Epic #2785 Phase 0 ported Spinr's brand *colors* into `admin-dashboard/globals.css` but deliberately deferred typography (Geist → Plus Jakarta Sans) and border-radius (10px → 16px) as a fast-follow. Phase 3 closes that gap. Along the way: `--font-sans` has pointed at `--font-geist-sans` since before this session, but nothing in this app ever actually defined that CSS variable (no `next/font` call loaded it) — so the app has been silently rendering the browser's default sans-serif the entire time, not literally Geist.

## 2. Root cause

Not a bug — deferred scope from Phase 0, plus a latent, unrelated gap (the dead `--font-geist-sans` reference) discovered while implementing this phase.

## 3. Fix / remediation

- Added a `next/font/google` `Plus_Jakarta_Sans` load in the root layout, exposed as `--font-plus-jakarta-sans` via `className`. Loaded unconditionally (cheap — just a variable font declaration) so no server round-trip is needed when a client toggles the flag.
- Added a `.theme-v2` CSS scope in `globals.css` that overrides `--font-geist-sans` (which `.font-sans` utilities actually read) and `--radius` (10px → 16px, i.e. `0.625rem` → `1rem`) — scoped to this class rather than `:root`/`.dark` so it's fully inert without it.
- Wired `useFeatureFlag('admin_theme_v2_enabled')` (built in PR #2837) into `dashboard/layout.tsx`: extracted a `DashboardShell` component (must be a descendant of `FeatureFlagsProvider` to read the flag) that conditionally adds the `theme-v2` class to the shell's outer div.

### A implementation detail worth flagging: `--font-sans` vs `--font-geist-sans`

My first attempt overrode `--font-sans` (the semantic alias), reasoning that's "the" font token. That doesn't work: Tailwind v4's `@theme inline` directive inlines the alias's *source* variable directly into generated utilities at build time — `.font-sans` compiles to `font-family: var(--font-geist-sans)`, not `var(--font-sans)`. A runtime override of `--font-sans` is therefore silently ineffective. Caught this by verifying live in a browser (see Verification below) rather than trusting the CSS in isolation — the fix was to override `--font-geist-sans` itself.

## 4. Risk & impact on existing functionality

- **Blast radius: isolated but shell-wide within the flag.** The `.theme-v2` class only ever appears when `admin_theme_v2_enabled` is `true` (defaults `false`, per PR #2837) — off, nothing changes anywhere. On, it affects `--radius`/`--font-geist-sans` (and everything derived from them: `rounded-*` utilities, `font-sans` utility) across every descendant of `DashboardShell`, i.e. all 34 dashboard routes — this is the intentional, understood scope of Phase 3, gated for exactly this reason.
- `dashboard/layout.tsx`: extracted `DashboardShell` is a pure refactor of the existing JSX (same className, same children structure) plus one added conditional class — no change to the auth-redirect effect, loading state, or unauthenticated early return.
- Root `layout.tsx`: the added `Plus_Jakarta_Sans` font load only appends a CSS variable class to `<body>`; the existing `font-sans antialiased` classes are unchanged, so nothing renders differently until `.theme-v2` is present.
- No other file reads `--font-geist-sans`, `--radius`, or the `.theme-v2` class name (grepped) besides the ones listed here and the ~25 shared `src/components/ui/` primitives that consume `--radius`-derived Tailwind utilities indirectly (which is the intended, flagged effect).

## 5. User-experience effect

- Internal-admin facing only, and only for whoever has the beta flag on (currently nobody — flag defaults off). When on: rounded corners become visibly larger (10px → 16px) and body text renders in Plus Jakarta Sans instead of the browser's default sans-serif, across every dashboard screen.
- Not visible mid-session to riders/drivers/corporate admins — no exposure on those surfaces.
- Toggling the flag mid-session (from the Settings page) does change already-open dashboard tabs for that admin within ~60s (the settings cache TTL) once they navigate/re-render, since the flag is polled once per `FeatureFlagsProvider` mount, not live-pushed — acceptable for a beta/canary toggle, not claimed as instant.

## 6. Files modified

| File path | What changed | Why |
|---|---|---|
| `admin-dashboard/src/app/layout.tsx` | Added `next/font/google` Plus Jakarta Sans load, exposed as `--font-plus-jakarta-sans` on `<body>` | Makes the new font available to the `.theme-v2` CSS scope without a runtime fetch |
| `admin-dashboard/src/app/globals.css` | Added `.theme-v2 { --font-geist-sans: var(--font-plus-jakarta-sans); --radius: 1rem; }` after the `.dark` block | The actual typography/radius port, scoped so it's inert without the class |
| `admin-dashboard/src/app/dashboard/layout.tsx` | Extracted `DashboardShell` (reads `useFeatureFlag`, applies `.theme-v2` conditionally) from `DashboardLayout`'s return JSX | `useFeatureFlag` must be called inside a descendant of `FeatureFlagsProvider`, not the component rendering the provider itself |

## 7. Before / after

```
# Before (admin-dashboard/src/app/dashboard/layout.tsx)
return (
    <FeatureFlagsProvider>
        <div className="min-h-screen bg-background">
            <Sidebar />
            <main className="transition-all duration-200 md:ml-[var(--sidebar-width,240px)]">
                <div className="p-4 pt-14 md:pt-6 md:p-8">{children}</div>
            </main>
        </div>
    </FeatureFlagsProvider>
);
```

```
# After
function DashboardShell({ children }) {
    const themeV2Enabled = useFeatureFlag("admin_theme_v2_enabled");
    return (
        <div className={`min-h-screen bg-background ${themeV2Enabled ? "theme-v2" : ""}`}>
            <Sidebar />
            <main className="transition-all duration-200 md:ml-[var(--sidebar-width,240px)]">
                <div className="p-4 pt-14 md:pt-6 md:p-8">{children}</div>
            </main>
        </div>
    );
}

// ...
return (
    <FeatureFlagsProvider>
        <DashboardShell>{children}</DashboardShell>
    </FeatureFlagsProvider>
);
```

## 8. Rollback plan

- Flip `admin_theme_v2_enabled` off from the Settings page (PR #2837) — takes effect within ~60s, no deploy needed. This is the primary, fast rollback path.
- If the flag mechanism itself needs to be bypassed entirely: revert this PR (`git revert`) — every change here is additive CSS/JS with no data or state mutation, fully safe to revert without a second deploy.

## 9. Verification performed

- [x] `npm run lint` (0 errors on the changed files) and a **real `npm run build`** — all 34 dashboard routes compiled successfully, both before and after the `--font-geist-sans` fix.
- [x] **Live browser verification, not just reasoning from the CSS**: ran a fresh production build (`npm run build` + `npm run start`, no dev-server cache involved) and used Playwright to read `getComputedStyle` on a probe element before/after toggling the `.theme-v2` class:
  - Before: `border-radius: 10px`, `font-family: ui-sans-serif, system-ui, sans-serif, ...` (confirming the current app really does render the browser default today, not Geist)
  - After: `border-radius: 16px`, `font-family: "Plus Jakarta Sans", "Plus Jakarta Sans Fallback"`
  - This live check is what caught the `--font-sans` vs `--font-geist-sans` bug in my first attempt (see section 3) — the initial version passed lint/build but silently didn't change the font at all, which only surfaced by actually reading computed styles in a browser.
- [x] Blast-radius grep performed: confirmed no other file reads `--font-geist-sans`/`.theme-v2` besides the three files changed here.
- [x] Reviewed against `CLAUDE.md`: feature-flag-for-blast-radius gate #3 (flag already existed from PR #2837, wired here), additive-over-destructive (new CSS scope, no existing token touched).
- [ ] Ran the changed code against real Supabase dev — n/a, this PR reads a flag PR #2837 already wired end-to-end; no new backend surface.

## What was NOT verified

- Not tested with a real logged-in admin session toggling the flag from the actual Settings page UI end-to-end — verified the CSS/JS mechanism directly (class toggle → computed style change) via Playwright against a production build instead, since this sandbox has no real Supabase/admin credentials to log in with.
- No screenshot/visual diff captured — no visual-regression baselines are seeded yet for admin-dashboard (tracked separately as #2809); reasoned from the Playwright computed-style check instead, which is a stronger signal than "no visible diff" reasoning but not a pixel-level screenshot.
- Did not verify behavior across older/non-Chromium browsers — `next/font/google`'s fallback font stack (`"Plus Jakarta Sans Fallback"`) is Next.js's standard mechanism for this and wasn't independently re-verified beyond trusting the framework.
