# Spinr branding assets

Place the real Spinr logo here as `spinr_logo.png` (recommend ≥600px wide,
transparent background) once available.

Until that file exists, `backend/utils/report_branding.py` falls back to a
text wordmark ("Spinr") rendered in the brand color/font on every branded
report — see `LOGO_PATH` and `has_logo_asset()` in that module. Drop the PNG
in and no code changes are needed; the fallback stops firing automatically.
