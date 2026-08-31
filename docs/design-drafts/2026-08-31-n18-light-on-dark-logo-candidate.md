# N18 — light-on-dark Spinr logo: candidate, not a decision

**This is a candidate for design review, not an approved asset.** N18's own
text frames this as "a design decision, not a code one" — this doc doesn't
override that; it gives the decision-maker something concrete to react to
instead of a blank page.

## What was done

`backend/static/branding/spinr_logo.png` (768×312, RGBA, transparent
background) has exactly two ink colors plus transparency, confirmed via
`Image.getcolors()`: a charcoal wordmark (~`#30343B`) and a red spiral "o"
(~`#E2403A`). Wrote a pixel-classification script (red-channel-dominance
test, not a naive full-image invert — a straight invert would also flip the
red into an off-brand cyan) that:

- Recolors the charcoal wordmark pixels to `#F2F2F7` — `.claude/context/brand-spinr.md`'s
  documented `darkColors.text` token, not an invented shade.
- Recolors the red spiral pixels to `#FF453A` — the same file's documented
  `darkColors.primary` (dark-mode brand red), not the light-mode `#FF3B30`.
- Preserves the original alpha channel per-pixel (anti-aliasing/edge
  softness carries over unchanged).

Output: `docs/design-drafts/spinr_logo_light_on_dark_CANDIDATE.png` (same
768×312 dimensions, transparent background) and a black-background preview
render, `..._preview_on_black.png`, for reviewing it the way it'd actually
appear in an email dark-mode header/footer.

## What this does NOT do

- **Not wired into any email template, app asset, or admin-dashboard
  branding path.** `email_layout.py`, the mobile app logo assets, and
  `backend/static/branding/README.md`'s resize convention are all
  untouched. Wiring this in is a separate, follow-up change *after* a real
  design decision, not bundled here.
- **Not a claim that this is the right treatment.** It's a mechanical
  recolor using the two documented brand tokens closest to the source
  colors — a plausible starting point, not the result of an actual design
  pass (contrast/legibility check at small sizes, whether the spiral's red
  reads correctly against `#000000` true-black vs. the `#1C1C1E` surface
  token, whether marketing wants a different treatment entirely).

## If this candidate is approved

1. Design/brand owner reviews `spinr_logo_light_on_dark_CANDIDATE.png`
   (and the black-background preview) and either approves, requests
   changes, or rejects it in favor of a different treatment.
2. Once approved, re-run the same resize convention
   `backend/static/branding/README.md` documents to produce the
   backend-static copy, and add the equivalent asset to
   `rider-app/assets/images/` / `driver-app/assets/images/` per
   `brand-spinr.md`'s logo section, matching the existing source-asset
   pattern (not hand-editing).
3. Wire it into `utils/email_layout.py`'s header/footer per N18's own
   framing — letting the header band and dark footer invert fully instead
   of staying light / using text-only.

## Acceptance (unchanged from N18's own text)

Still a design decision. This candidate doesn't close N18's checkbox —
it exists so the decision has a real option to look at.
