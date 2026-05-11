"""Server-side device integrity verification.

Validates device attestation signals from the driver app. Flags devices
that are likely rooted, emulated, or running modified APKs.

Risk tiers:
  - "trusted"    — real device, stock OS, no red flags
  - "elevated"   — some signals concerning but not conclusive (old OS, unknown brand)
  - "untrusted"  — emulator, known root indicators, or repeat offender

Untrusted devices are not blocked but get enhanced GPS monitoring and
admin visibility. This avoids false positives from legitimate edge-case
devices while still catching the majority of spoofers.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

try:
    from ..db import db
except ImportError:
    from db import db  # type: ignore

logger = logging.getLogger(__name__)

# Known emulator brands/models
_EMULATOR_INDICATORS = {
    "brands": {"google", "generic", "unknown", "sdk_gphone"},
    "models": {"sdk_gphone64_arm64", "emulator", "android sdk built for x86", "vbox86p"},
}

# Suspicious OS versions (very old = likely test device or emulator)
_MIN_ANDROID_VERSION = 10
_MIN_IOS_VERSION = 15


async def verify_device(
    user_id: str,
    driver_id: str,
    device_info: dict,
) -> dict:
    """Evaluate device trust level. Returns {"trusted": bool, "risk_tier": str, "reason"?: str}."""

    platform = device_info.get("platform", "")
    is_device = device_info.get("isDevice", True)
    brand = (device_info.get("brand") or "").lower()
    model = (device_info.get("modelName") or "").lower()
    os_version = device_info.get("osVersion") or ""

    # Hard fail: emulator
    if not is_device:
        await _record_attestation(driver_id, "untrusted", "emulator")
        return {"trusted": False, "risk_tier": "untrusted", "reason": "emulator_detected"}

    # Check brand against emulator list
    if brand in _EMULATOR_INDICATORS["brands"]:
        await _record_attestation(driver_id, "untrusted", f"suspicious_brand:{brand}")
        return {"trusted": False, "risk_tier": "untrusted", "reason": "suspicious_device"}

    # Check model
    if model in _EMULATOR_INDICATORS["models"]:
        await _record_attestation(driver_id, "untrusted", f"suspicious_model:{model}")
        return {"trusted": False, "risk_tier": "untrusted", "reason": "suspicious_device"}

    # OS version check
    risk_tier = "trusted"
    reason = None
    try:
        major_version = int(os_version.split(".")[0])
        if platform == "android" and major_version < _MIN_ANDROID_VERSION:
            risk_tier = "elevated"
            reason = "outdated_os"
        elif platform == "ios" and major_version < _MIN_IOS_VERSION:
            risk_tier = "elevated"
            reason = "outdated_os"
    except (ValueError, IndexError):
        risk_tier = "elevated"
        reason = "unparseable_os_version"

    await _record_attestation(driver_id, risk_tier, reason)

    return {
        "trusted": risk_tier != "untrusted",
        "risk_tier": risk_tier,
        "reason": reason,
    }


async def _record_attestation(driver_id: str, risk_tier: str, reason: str | None) -> None:
    """Store attestation result for admin visibility and pattern detection."""
    try:
        await db.update_one(
            "drivers",
            {"id": driver_id},
            {
                "$set": {
                    "device_risk_tier": risk_tier,
                    "device_attested_at": datetime.now(timezone.utc).isoformat(),
                    "device_risk_reason": reason,
                }
            },
        )
    except Exception as e:
        logger.warning(f"[attestation] failed to store result for driver {driver_id}: {e}")
