"""
Receipt generator for Spinr rides.
Generates HTML receipt and sends via email (SendGrid when configured, logs otherwise).
"""

import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def generate_receipt_html(ride: dict, rider: dict, driver: dict = None, tip: float = 0) -> str:
    """Generate HTML receipt for a completed ride."""
    fare = ride.get("total_fare", 0) or 0
    total = fare + tip
    rider_name = f"{rider.get('first_name', '')} {rider.get('last_name', '')}".strip() or "Rider"
    driver_name = "Unknown"
    if driver:
        driver_name = f"{driver.get('first_name', '')} {driver.get('last_name', '')}".strip() or driver.get(
            "name", "Driver"
        )

    ride_date = ride.get("ride_completed_at") or ride.get("created_at") or ""
    if ride_date:
        try:
            dt = datetime.fromisoformat(str(ride_date).replace("Z", "+00:00").replace("+00:00", ""))
            ride_date = dt.strftime("%B %d, %Y at %I:%M %p")
        except Exception:  # noqa: S110
            pass

    return f"""
    <!DOCTYPE html>
    <html>
    <head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"></head>
    <body style="margin:0;padding:0;background:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
    <table width="100%" cellpadding="0" cellspacing="0" style="max-width:520px;margin:20px auto;background:#fff;border-radius:16px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);">
        <!-- Header -->
        <tr><td style="background:#ee2b2b;padding:28px 24px;text-align:center;">
        <h1 style="color:#fff;margin:0;font-size:28px;font-weight:800;letter-spacing:-0.5px;">Spinr</h1>
          <p style="color:rgba(255,255,255,0.8);margin:6px 0 0;font-size:14px;">Ride Receipt</p>
        </td></tr>

        <!-- Greeting -->
        <tr><td style="padding:24px 24px 0;">
        <p style="color:#1a1a1a;font-size:16px;margin:0;">Hi {rider_name},</p>
          <p style="color:#888;font-size:14px;margin:6px 0 0;">Thanks for riding with Spinr. Here's your receipt.</p>
        </td></tr>

        <!-- Amount -->
        <tr><td style="padding:20px 24px;text-align:center;">
        <p style="color:#ee2b2b;font-size:42px;font-weight:800;margin:0;">${total:.2f} CAD</p>
          <p style="color:#999;font-size:12px;margin:4px 0 0;">{ride_date}</p>
        </td></tr>

        <!-- Route -->
        <tr><td style="padding:0 24px 16px;">
        <table width="100%" style="background:#f9f9f9;border-radius:12px;padding:16px;">
            <tr>
            <td style="width:24px;vertical-align:top;padding:4px 12px 4px 0;">
                <div style="width:10px;height:10px;border-radius:5px;background:#10b981;margin:4px auto;"></div>
                <div style="width:2px;height:24px;background:#ddd;margin:0 auto;"></div>
                <div style="width:10px;height:10px;border-radius:5px;background:#ee2b2b;margin:0 auto;"></div>
                </td>
              <td>
                <p style="color:#999;font-size:10px;margin:0;text-transform:uppercase;letter-spacing:0.5px;">Pickup</p>
                <p style="color:#1a1a1a;font-size:14px;margin:2px 0 16px;font-weight:500;">{ride.get("pickup_address", "N/A")}</p>
                <p style="color:#999;font-size:10px;margin:0;text-transform:uppercase;letter-spacing:0.5px;">Dropoff</p>
                <p style="color:#1a1a1a;font-size:14px;margin:2px 0 0;font-weight:500;">{ride.get("dropoff_address", "N/A")}</p>
                </td>
            </tr>
            </table>
        </td></tr>

        <!-- Fare Breakdown -->
        <tr><td style="padding:0 24px 16px;">
        <table width="100%" style="font-size:14px;">
            <tr><td style="color:#666;padding:4px 0;">Base fare</td><td style="text-align:right;color:#1a1a1a;">${ride.get("base_fare", 0) or 0:.2f}</td></tr>
            <tr><td style="color:#666;padding:4px 0;">Distance ({ride.get("distance_km", 0):.1f} km)</td><td style="text-align:right;color:#1a1a1a;">${ride.get("distance_fare", 0) or 0:.2f}</td></tr>
            <tr><td style="color:#666;padding:4px 0;">Time ({ride.get("duration_minutes", 0)} min)</td><td style="text-align:right;color:#1a1a1a;">${ride.get("time_fare", 0) or 0:.2f}</td></tr>
            <tr><td style="color:#666;padding:4px 0;">Booking fee</td><td style="text-align:right;color:#1a1a1a;">${ride.get("booking_fee", 0) or 0:.2f}</td></tr>
            {'<tr><td style="color:#10b981;padding:4px 0;">Tip</td><td style="text-align:right;color:#10b981;">$' + f"{tip:.2f}" + "</td></tr>" if tip > 0 else ""}
            <tr><td colspan="2" style="border-top:1px solid #eee;padding:0;"></td></tr>
            <tr><td style="color:#1a1a1a;padding:8px 0;font-weight:700;font-size:16px;">Total</td><td style="text-align:right;color:#ee2b2b;font-weight:800;font-size:18px;">${total:.2f}</td></tr>
            </table>
        </td></tr>

        <!-- Driver -->
        <tr><td style="padding:0 24px 16px;">
        <table width="100%" style="background:#f9f9f9;border-radius:12px;padding:12px 16px;">
            <tr>
            <td style="width:40px;"><div style="width:36px;height:36px;border-radius:18px;background:#e8e8e8;text-align:center;line-height:36px;color:#888;font-weight:700;">{driver_name[0] if driver_name else "?"}</div></td>
              <td style="padding-left:12px;">
                <p style="margin:0;font-size:14px;font-weight:600;color:#1a1a1a;">{driver_name}</p>
                <p style="margin:2px 0 0;font-size:12px;color:#999;">Your driver</p>
                </td>
            </tr>
            </table>
        </td></tr>

        <!-- Footer -->
        <tr><td style="padding:16px 24px 24px;text-align:center;border-top:1px solid #f0f0f0;">
        <p style="color:#bbb;font-size:12px;margin:0;">Spinr Technologies Inc. · Saskatoon, SK</p>
          <p style="color:#bbb;font-size:11px;margin:4px 0 0;">support@spinr.ca · www.spinr.ca</p>
        </td></tr>
        </table>
    </body>
    </html>
    """


async def send_receipt_email(ride: dict, rider: dict, driver: dict = None, tip: float = 0):
    """Send receipt email. Uses SendGrid when configured, logs otherwise."""
    email = rider.get("email", "")
    if not email:
        logger.warning(f"No email for rider {rider.get('id')} — skipping receipt")
        return False

    html = generate_receipt_html(ride, rider, driver, tip)
    total = (ride.get("total_fare", 0) or 0) + tip

    # Try SendGrid
    try:
        from ..settings_loader import get_app_settings

        settings = await get_app_settings()
        sendgrid_key = settings.get("sendgrid_api_key", "")

        if sendgrid_key:
            import httpx

            response = await httpx.AsyncClient().post(
                "https://api.sendgrid.com/v3/mail/send",
                headers={"Authorization": f"Bearer {sendgrid_key}", "Content-Type": "application/json"},
                json={
                    "personalizations": [{"to": [{"email": email}]}],
                    "from": {"email": "receipts@spinr.ca", "name": "Spinr"},
                    "subject": f"Your Spinr ride receipt — ${total:.2f}",
                    "content": [{"type": "text/html", "value": html}],
                },
            )
            logger.info(f"[EMAIL] SendGrid receipt sent to {email} (status: {response.status_code})")
            return response.status_code in (200, 201, 202)
    except ImportError:
        pass
    except Exception as e:
        logger.warning(f"[EMAIL] SendGrid failed: {e}")

    # Fallback: log only
    logger.info(f"[EMAIL] Receipt for ride {ride.get('id')} → {email} | Total: ${total:.2f} (SendGrid not configured)")
    return False
