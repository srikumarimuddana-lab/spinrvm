from fastapi import APIRouter

try:
    from ..settings_loader import get_app_settings
except ImportError:
    from settings_loader import get_app_settings

api_router = APIRouter(tags=["Settings"])


# Placeholder legal text. Returned by `/settings/legal` when the DB
# `settings` row has empty or missing `terms_of_service_text` /
# `privacy_policy_text` values. Clearly marked as a placeholder so
# nobody confuses it with real legal copy, but still useful enough
# to let the mobile apps' legal screens render something on first
# launch without blocking internal testing on operations writing
# the real text.
#
# Admins can replace these values via the admin dashboard
# Settings → Legal tab (PUT /api/admin/settings). Once the real
# text is saved, this placeholder stops being returned and the
# mobile apps pick up the live copy on next fetch.
_TOS_PLACEHOLDER = """SPINR — TERMS OF SERVICE (PLACEHOLDER)

This text is a development placeholder. Your operations team must
replace it with real Terms of Service before launching to the public.

By using the Spinr app you agree to the terms published on our
website. For questions contact support.

Last updated: not yet finalized."""

_PRIVACY_PLACEHOLDER = """SPINR — PRIVACY POLICY (PLACEHOLDER)

This text is a development placeholder. Your operations team must
replace it with a real Privacy Policy before launching to the public.

In summary, Spinr collects the minimum information needed to match
riders with drivers (phone number, location, payment method, ride
history) and uses it only to operate the service. We do not sell
personal data. For questions contact support.

Last updated: not yet finalized."""


@api_router.get("/settings")
async def get_public_settings():
    settings = await get_app_settings()
    return {
        "google_maps_api_key": settings.get("google_maps_api_key", ""),
        "stripe_publishable_key": settings.get("stripe_publishable_key", ""),
        # Public base URL for the "Share Trip" tracking page (admin-dashboard
        # `/track/[token]`). Set via the admin Settings panel so the URL can
        # be rotated without a mobile rebuild. Empty string until configured.
        "track_base_url": settings.get("track_base_url", ""),
        # Driver SOS discreet-hold-shield rollout gate (ACTION_ITEMS.md
        # B16) -- dark-launched, driver-app only. Default False = the
        # driver dashboard keeps rendering the existing shared SOSButton.
        "driver_discreet_sos_enabled": bool(settings.get("driver_discreet_sos_enabled", False)),
    }


@api_router.get("/settings/legal")
async def get_legal_settings():
    """Return the current Terms of Service + Privacy Policy text.

    Falls back to a clearly-marked placeholder when the DB columns
    are empty so the rider/driver legal screens have something to
    render during internal testing. The placeholder is replaced
    automatically once the admin dashboard writes real text to the
    `settings` row.
    """
    settings = await get_app_settings()
    tos = (settings.get("terms_of_service_text") or "").strip()
    privacy = (settings.get("privacy_policy_text") or "").strip()
    return {
        "terms_of_service_text": tos if tos else _TOS_PLACEHOLDER,
        "privacy_policy_text": privacy if privacy else _PRIVACY_PLACEHOLDER,
    }


@api_router.get("/company-info")
async def get_company_info():
    """Public company / support info embedded by rider + driver apps.

    Business-card data — nothing sensitive. Surfaced in the Support
    screen of the rider app and the Profile screen of the driver app
    so ops can update address / phone / email in one place (admin
    Settings → Company Info) and both apps pick it up on next fetch.
    """
    settings = await get_app_settings()
    return {
        "name": settings.get("company_name", "Spinr") or "Spinr",
        "address": settings.get("company_address", "") or "",
        "phone": settings.get("company_phone", "") or "",
        "email": settings.get("company_email", "") or "",
        "website": settings.get("company_website", "") or "",
    }
