"""Canonical user-facing error message keys.

Every key has an i18n entry in rider-app/i18n/en.json and driver-app/i18n/en.json
under the same dotted path. Keys are stable; renaming one is a coordinated change
across backend + both mobile apps.
"""

from typing import Final


class ErrorKeys:
    """Use these constants in raise sites — never inline string literals."""

    # Auth
    AUTH_ACCOUNT_SUSPENDED: Final[str] = "errors.auth.account_suspended"
    AUTH_OTP_INVALID: Final[str] = "errors.auth.otp_invalid"
    AUTH_OTP_EXPIRED: Final[str] = "errors.auth.otp_expired"
    AUTH_OTP_LOCKED: Final[str] = "errors.auth.otp_locked"
    AUTH_INVALID_CREDENTIALS: Final[str] = "errors.auth.invalid_credentials"
    AUTH_TOKEN_EXPIRED: Final[str] = "errors.auth.token_expired"

    # Driver onboarding / state
    DRIVER_DOCUMENTS_EXPIRED: Final[str] = "errors.driver.documents_expired"
    DRIVER_DOCUMENTS_PENDING: Final[str] = "errors.driver.documents_pending"
    DRIVER_DOCUMENTS_REJECTED: Final[str] = "errors.driver.documents_rejected"
    DRIVER_SUBSCRIPTION_REQUIRED: Final[str] = "errors.driver.subscription_required"
    DRIVER_QUOTA_EXHAUSTED: Final[str] = "errors.driver.quota_exhausted"
    DRIVER_NOT_AVAILABLE: Final[str] = "errors.driver.not_available"
    DRIVER_OFFLINE: Final[str] = "errors.driver.offline"
    DRIVER_DUAL_RUN_HOLD: Final[str] = "errors.driver.dual_run_hold"
    DRIVER_LICENSE_CLASS_INELIGIBLE: Final[str] = "errors.driver.license_class_ineligible"
    DRIVER_VEHICLE_TOO_OLD: Final[str] = "errors.driver.vehicle_too_old"

    # Ride
    RIDE_NOT_FOUND: Final[str] = "errors.ride.not_found"
    RIDE_ALREADY_CANCELLED: Final[str] = "errors.ride.already_cancelled"
    RIDE_INVALID_STATUS: Final[str] = "errors.ride.invalid_status"
    RIDE_NO_DRIVERS_AVAILABLE: Final[str] = "errors.ride.no_drivers_available"
    RIDE_PRICE_MISMATCH: Final[str] = "errors.ride.price_mismatch"
    RIDE_TAKEN: Final[str] = "errors.ride.taken"

    # Payment / wallet
    PAYMENT_FAILED: Final[str] = "errors.payment.failed"
    PAYMENT_METHOD_INVALID: Final[str] = "errors.payment.method_invalid"
    PAYMENT_INSUFFICIENT_FUNDS: Final[str] = "errors.payment.insufficient_funds"
    PAYMENT_REFUND_FAILED: Final[str] = "errors.payment.refund_failed"
    PAYMENT_UNPAID_RIDE_BLOCK: Final[str] = "errors.payment.unpaid_ride_block"

    # Profile / account
    PROFILE_EMAIL_IN_USE: Final[str] = "errors.profile.email_in_use"
    PROFILE_EMAIL_MISSING: Final[str] = "errors.profile.email_missing"
    PROFILE_EMAIL_VERIFICATION_STALE: Final[str] = "errors.profile.email_verification_stale"

    # System
    SYSTEM_INTERNAL: Final[str] = "errors.system.internal"
    SYSTEM_SERVICE_UNAVAILABLE: Final[str] = "errors.system.service_unavailable"
    SYSTEM_RATE_LIMITED: Final[str] = "errors.system.rate_limited"
    SYSTEM_DATABASE: Final[str] = "errors.system.database"
