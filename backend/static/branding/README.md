# Spinr branding assets

`spinr_logo.png` — the bullseye mark + "spinr" wordmark, transparent
background, 768×312px — embedded in every Spinr-branded report (PDF header,
Excel/Word title block via `report_branding.py`). This is the real brand
asset, copied from `driver-app/assets/images/spinr-logo.png` /
`rider-app/assets/images/spinr-logo.png` (the same file used live in both
mobile apps) and upscaled 2x with Lanczos resampling for print/PDF
resolution — replacing an earlier placeholder recreation this directory
shipped with. If the mobile apps' source logo is ever replaced with a
higher-resolution or updated version, re-run the same resize from that
file rather than hand-editing this one.

If this file is ever removed, `report_branding.has_logo_asset()` returns
`False` and PDF headers render without an image (title text only, no
fallback wordmark is drawn in its place) — see `new_branded_pdf()`.
