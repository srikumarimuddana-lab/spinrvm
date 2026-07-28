# Spinr branding assets

`spinr_logo.png` — the bullseye mark + "spinr" wordmark, transparent
background, 684×260px — embedded in every Spinr-branded report (PDF header,
Excel/Word title block via `report_branding.py`). This is a clean
recreation matching the brand's established red (`report_branding.BRAND_RGB`,
#ee2b2b) and wordmark style; replace with an official design-team asset
when one exists — no code change needed, `report_branding.LOGO_PATH` just
points at this filename.

If this file is ever removed, `report_branding.has_logo_asset()` returns
`False` and PDF headers render without an image (title text only, no
fallback wordmark is drawn in its place) — see `new_branded_pdf()`.
