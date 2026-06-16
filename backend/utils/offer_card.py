"""Render a branded ride-offer "fare banner" PNG for the driver notification.

Shown as the Android ``BigPicture`` image in the heads-up ride-offer card so a
driver reads the whole offer at a glance instead of a bare text row. Rendered
lazily, on demand, by ``routes/offer_card.py`` (never on the dispatch hot path)
so it adds zero latency to offer delivery.

PIPEDA: the banner shows only minimised rider info — first name + coarse area
(street/locality, never the house number or unit). See ``coarsen_address`` and
``first_name``. No phone, no email, no exact address.

Fonts are bundled (``backend/assets/fonts/DejaVu*``) rather than relying on the
OS — Pillow ships no fonts and the production images can't be assumed to carry
any. Markers are drawn as shapes, not emoji, because DejaVu has no colour-emoji
glyphs (they'd render as tofu boxes).

``render_offer_card`` never raises — it returns ``None`` on any failure so the
notification silently falls back to the BigText card.
"""

from __future__ import annotations

import io
import logging
import os
import re
from typing import Optional

logger = logging.getLogger(__name__)

_W, _H = 1024, 512
_FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "fonts")
_FONT_REGULAR = os.path.join(_FONT_DIR, "DejaVuSans.ttf")
_FONT_BOLD = os.path.join(_FONT_DIR, "DejaVuSans-Bold.ttf")

# Spinr palette.
_BG = (11, 18, 32)            # deep navy
_PANEL = (19, 28, 46)         # slightly lighter card panel
_GREEN = (0, 210, 106)        # brand green / pickup marker
_RED = (255, 59, 48)          # dropoff marker
_WHITE = (245, 247, 250)
_MUTED = (148, 163, 184)      # slate-400


# Strip a leading street number and unit/apt token so the banner shows the
# street + locality ("Victoria Ave, Regina") rather than the exact doorstep
# ("1855 Victoria Ave #3, Regina") — PIPEDA data minimisation for an image
# that is fetched without app auth.
_LEADING_NUMBER = re.compile(r"^\s*\d+[a-zA-Z]?\s+")
_UNIT_TOKEN = re.compile(r"(?:#\s*\w+|\b(?:apt\.?|apartment|unit|suite|ste\.?)\s*\w+)", re.IGNORECASE)


def coarsen_address(addr: Optional[str]) -> str:
    """Return a coarse, PII-minimised form of a street address."""
    if not addr:
        return ""
    out = _UNIT_TOKEN.sub("", str(addr))
    out = _LEADING_NUMBER.sub("", out)
    # Drop empty comma-segments left behind by the stripped unit/number.
    parts = [p.strip() for p in out.split(",")]
    out = ", ".join(p for p in parts if p)
    return re.sub(r"\s{2,}", " ", out).strip()


def first_name(name: Optional[str]) -> str:
    """Return only the first token of a display name."""
    if not name:
        return ""
    return str(name).strip().split()[0] if str(name).strip() else ""


def _font(path: str, size: int):
    from PIL import ImageFont

    try:
        return ImageFont.truetype(path, size)
    except Exception:  # noqa: BLE001 — fall back to Pillow's bitmap default
        return ImageFont.load_default()


def _ellipsize(draw, text: str, font, max_w: int) -> str:
    if not text:
        return ""
    if draw.textlength(text, font=font) <= max_w:
        return text
    ell = "…"
    while text and draw.textlength(text + ell, font=font) > max_w:
        text = text[:-1]
    return (text + ell) if text else ell


def _pill_right(draw, right_x, y, text, font, *, fg, bg):
    """Draw a rounded badge right-anchored at ``right_x``; return its height."""
    pad_x, pad_y = 18, 8
    tw = draw.textlength(text, font=font)
    w = tw + pad_x * 2
    h = font.size + pad_y * 2
    x = right_x - w
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=bg)
    draw.text((x + pad_x, y + pad_y - 2), text, font=font, fill=fg)
    return h


def render_offer_card(
    *,
    fare: float,
    distance_km: Optional[float] = None,
    duration_minutes: Optional[float] = None,
    rider_first_name: Optional[str] = None,
    rider_rating: Optional[float] = None,
    pickup_area: Optional[str] = None,
    dropoff_area: Optional[str] = None,
    total_bonus: Optional[float] = None,
) -> Optional[bytes]:
    """Render the offer banner to PNG bytes. Returns None on any failure.

    Surge is intentionally never drawn — it is not surfaced to drivers on the
    offer card. The fare already reflects any surge.
    """
    try:
        from PIL import Image, ImageDraw
    except Exception as exc:  # noqa: BLE001
        logger.warning("Pillow unavailable; skipping offer card: %s", exc)
        return None

    try:
        img = Image.new("RGB", (_W, _H), _BG)
        d = ImageDraw.Draw(img)

        f_label = _font(_FONT_BOLD, 30)
        f_fare = _font(_FONT_BOLD, 132)
        f_row = _font(_FONT_BOLD, 40)
        f_row_sub = _font(_FONT_REGULAR, 34)
        f_pill = _font(_FONT_BOLD, 30)
        f_brand = _font(_FONT_BOLD, 34)

        margin = 56

        # Header label.
        d.text((margin, 44), "NEW RIDE REQUEST", font=f_label, fill=_MUTED)

        # Fare — the headline.
        fare_text = f"${float(fare or 0):.2f}"
        d.text((margin, 78), fare_text, font=f_fare, fill=_GREEN)
        fare_right = margin + d.textlength(fare_text, font=f_fare)

        # Bonus pill — right-anchored beside the fare. Surge is deliberately
        # not shown to drivers; only the (already surge-inclusive) fare is.
        _ = fare_right  # fare width computed for layout symmetry; pill is right-aligned
        if total_bonus and total_bonus > 0:
            _pill_right(
                d, _W - margin, 112, f"+${float(total_bonus):.2f} BONUS", f_pill,
                fg=_BG, bg=_GREEN,
            )

        # Route panel.
        panel_top = 246
        d.rounded_rectangle([margin, panel_top, _W - margin, panel_top + 150], radius=24, fill=_PANEL)
        text_x = margin + 86
        max_text_w = (_W - margin) - text_x - 32

        def _stop(cy, color, label, area):
            r = 11
            d.ellipse([margin + 40 - r, cy - r, margin + 40 + r, cy + r], fill=color)
            d.text((text_x, cy - 34), label, font=f_row_sub, fill=_MUTED)
            d.text((text_x + 132, cy - 36), _ellipsize(d, area or "—", f_row, max_text_w - 132),
                   font=f_row, fill=_WHITE)

        _stop(panel_top + 44, _GREEN, "Pickup", pickup_area)
        # Connector line between the two stops.
        d.line([margin + 40, panel_top + 58, margin + 40, panel_top + 92], fill=_MUTED, width=3)
        _stop(panel_top + 106, _RED, "Dropoff", dropoff_area)

        # Bottom stats row: distance • time • rider (★ rating).
        stats = []
        if distance_km is not None:
            stats.append(f"{float(distance_km):.1f} km")
        if duration_minutes is not None:
            stats.append(f"{round(float(duration_minutes))} min")
        rn = first_name(rider_first_name)
        if rn:
            stats.append(f"{rn} {rider_rating:.1f}★" if rider_rating else rn)
        stats_text = "   •   ".join(stats)
        if stats_text:
            d.text((margin, _H - 78), stats_text, font=f_row_sub, fill=_MUTED)

        # Brand wordmark, bottom-right.
        brand = "spinr"
        bw = d.textlength(brand, font=f_brand)
        d.text((_W - margin - bw, _H - 74), brand, font=f_brand, fill=_GREEN)

        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Offer card render failed: %s", exc)
        return None
