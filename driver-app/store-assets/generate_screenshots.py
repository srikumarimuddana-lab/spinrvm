#!/usr/bin/env python3
"""Generate Google Play / App Store phone screenshots for Spinr Driver.

Renders 8 marketing artboards (1080x1920) with headless Chromium. The phone
mock-ups reproduce the real driver-app screens -- copy is taken from
driver-app/i18n/en.json and the components under driver-app/components/, so
the store listing shows what the app actually renders.

Usage:
    python3 driver-app/store-assets/generate_screenshots.py

Output: driver-app/store-assets/screenshots/android-phone-NN-<screen>.png

Brand font: Plus Jakarta Sans (per .claude/context/brand-spinr.md). Drop any
Plus Jakarta Sans .ttf files into driver-app/assets/fonts/ and re-run to embed
them; without them the render falls back to the closest installed grotesque.
"""
import base64
import pathlib
import struct
import zlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
APP = HERE.parent
OUT = HERE / "screenshots"

REF_W, REF_H = 1080, 1920          # the artboard every proportion is derived from

# Store artboards, keyed by exact pixel size because that is what App Store
# Connect validates -- an inch label ("6.7-inch") maps to more than one pixel
# size, and uploading the wrong one to a slot is rejected. Apple wants an iPhone
# size plus an iPad size when the app ships with supportsTablet
# (driver-app/app.config.ts sets it true); Google Play takes the 1080x1920 phone
# set. See the size table in README.md for which slot each one fills.
ARTBOARDS = [
    ("android-1080x1920", 1080, 1920, "punch"),   # Google Play phone
    ("ios-1320x2868", 1320, 2868, "island"),      # 6.9" slot
    ("ios-1290x2796", 1290, 2796, "island"),      # 6.9" slot (alternate)
    ("ios-1284x2778", 1284, 2778, "island"),      # 6.5" slot
    ("ios-1242x2208", 1242, 2208, "island"),      # 5.5" slot
    ("ipad-2048x2732", 2048, 2732, "island"),     # iPad 12.9"/13" slot
]

# Brand tokens -- shared/theme/index.ts via .claude/context/brand-spinr.md.
RED = "#FF3B30"          # primary / brand red
RED_DARK = "#D32F2F"     # primaryDark, contrast-safe
INK = "#14171A"          # headline ink
TEXT = "#1A1A1A"
MUTED = "#6B7280"
BORDER = "#E5E7EB"
SUCCESS = "#10B981"
SUCCESS_DARK = "#059669"
WARNING = "#F59E0B"
ORANGE = "#FF9500"
CREAM = "#FBF4F1"        # marketing canvas (matches rider-app store set)
PINK = "#FAE2DD"
PINK_LINE = "#F7D3CC"

CHROME_CANDIDATES = [
    "/opt/pw-browsers/chromium-1194/chrome-linux/chrome",
    "/opt/pw-browsers/chromium/chrome-linux/chrome",
    "chromium", "chromium-browser", "google-chrome",
]


def find_chrome():
    for c in CHROME_CANDIDATES:
        p = shutil.which(c) if not c.startswith("/") else (c if pathlib.Path(c).exists() else None)
        if p:
            return p
    sys.exit("No Chromium/Chrome binary found. Set one in CHROME_CANDIDATES.")


def data_uri(path, mime):
    return "data:%s;base64,%s" % (mime, base64.b64encode(pathlib.Path(path).read_bytes()).decode())


def font_face_css():
    """Embed Plus Jakarta Sans if the .ttf files are present locally."""
    fonts_dir = APP / "assets" / "fonts"
    faces, found = [], []
    weights = {"Regular": 400, "Medium": 500, "SemiBold": 600, "Bold": 700, "ExtraBold": 800}
    for name, weight in weights.items():
        for pat in ("PlusJakartaSans-%s.ttf" % name, "PlusJakartaSans_%s.ttf" % name):
            f = fonts_dir / pat
            if f.exists():
                faces.append(
                    "@font-face{font-family:'Plus Jakarta Sans';font-style:normal;"
                    "font-weight:%d;src:url(%s) format('truetype');}" % (weight, data_uri(f, "font/ttf"))
                )
                found.append(name)
                break
    if found:
        print("  embedding Plus Jakarta Sans: %s" % ", ".join(found))
    else:
        print("  NOTE: Plus Jakarta Sans not found in driver-app/assets/fonts/ -- "
              "falling back to an installed grotesque.")
    return "\n".join(faces)


# --- artboard geometry ----------------------------------------------------
# Screen UI is authored in a 390-wide logical space (a real phone) and scaled
# into the mock-up frame, so the in-phone UI can use natural sizes. Every
# artboard derives from the same proportions, so one layout serves all sizes.
UI_W = 390
# Measured off the live rider-app listing screenshot (1290x2796): frame spans
# x 179..1110, y 765..2740. Keep these in step with that set -- a taller frame
# reads as an elongated slab rather than a phone.
FRAME_ASPECT = 931 / 1975          # 1:2.12, a real handset body
FRAME_H = 1975 / 2796              # frame height as a fraction of artboard height
BOTTOM_GAP = 56 / 2796             # canvas left visible below the frame


class Board:
    """Geometry for one artboard size."""

    def __init__(self, key, w, h, notch):
        self.key, self.w, self.h, self.notch = key, w, h, notch
        # Scale content by whichever axis is tighter, so a wide canvas (iPad)
        # doesn't blow the type up past the space above the phone.
        self.s = min(w / float(REF_W), h / float(REF_H))
        self.ph_h = round(FRAME_H * h)
        self.ph_w = round(self.ph_h * FRAME_ASPECT)
        self.ph_top = h - round(BOTTOM_GAP * h) - self.ph_h
        self.ph_left = (w - self.ph_w) // 2
        f = self.ph_w / 670.0          # frame-local scale (bezel, radius, notch)
        self.bezel = max(6, round(13 * f))
        self.scr_w = self.ph_w - 2 * self.bezel
        self.scr_h = self.ph_h - 2 * self.bezel
        self.scale = self.scr_w / float(UI_W)
        self.ui_h = round(self.scr_h / self.scale)
        self.radius = round(58 * f)
        self.sradius = round(46 * f)
        if notch == "island":
            self.notch_w = round(0.26 * self.scr_w)
            self.notch_h = round(self.notch_w / 3.3)
            self.notch_top = round(0.012 * self.scr_h)
        else:                                   # Android punch-hole camera
            self.notch_w = self.notch_h = round(13 * f)
            self.notch_top = round(15 * f)
        self.notch_r = self.notch_h // 2
        self.hi_w = round(0.34 * self.scr_w)
        self.hi_h = max(3, round(0.006 * self.scr_h))
        self.hi_bottom = round(0.009 * self.scr_h)

    def px(self, v):
        """Scale a reference-artboard measurement to this board."""
        return round(v * self.s)


def css(b):
    return """
%(fonts)s
*{margin:0;padding:0;box-sizing:border-box;}
html,body{width:%(W)dpx;height:%(H)dpx;overflow:hidden;}
body{font-family:'Plus Jakarta Sans','Liberation Sans','DejaVu Sans',sans-serif;
  -webkit-font-smoothing:antialiased;text-rendering:geometricPrecision;}
.card{position:relative;width:%(W)dpx;height:%(H)dpx;overflow:hidden;}

/* ---------- light marketing card ---------- */
.card.light{background:%(CREAM)s;}
.blob-r{position:absolute;width:%(blob)dpx;height:%(blob)dpx;border-radius:50%%;
  background:%(PINK)s;right:-%(blobX)dpx;top:%(blobY)dpx;}
.blob-l{position:absolute;width:%(blobl)dpx;height:%(blobl)dpx;border-radius:50%%;
  border:%(blobB)dpx solid %(PINK_LINE)s;left:-%(bloblX)dpx;bottom:-%(bloblY)dpx;}
/* header block is centred in the space above the frame, so one layout serves
   every artboard aspect instead of hard-coded tops per size */
.head{position:absolute;left:0;right:0;top:0;height:%(headH)dpx;display:flex;
  flex-direction:column;align-items:center;justify-content:center;}
.logo{width:%(logoW)dpx;margin-bottom:%(logoGap)dpx;}
h1{text-align:center;font-size:%(h1)dpx;line-height:0.98;font-weight:800;
  letter-spacing:-0.035em;color:%(INK)s;margin-bottom:%(h1Gap)dpx;}
h1 em{font-style:normal;color:%(RED)s;}
.sub{text-align:center;font-size:%(sub)dpx;line-height:1.41;font-weight:500;
  color:%(MUTED)s;letter-spacing:-0.005em;padding:0 %(subPad)dpx;}

/* ---------- phone frame ---------- */
.phone{position:absolute;width:%(PH_W)dpx;height:%(PH_H)dpx;background:#0B0B0C;
  border-radius:%(radius)dpx;padding:%(BEZEL)dpx;
  box-shadow:0 %(shadY)dpx %(shadB)dpx rgba(30,10,8,.22),0 %(shad2)dpx %(shad3)dpx rgba(30,10,8,.10);}
.phone.center{left:%(PH_LEFT)dpx;top:%(PH_TOP)dpx;}
.screen{position:relative;width:%(SCR_W)dpx;height:%(SCR_H)dpx;background:#fff;
  border-radius:%(sradius)dpx;overflow:hidden;}
.ui{position:absolute;top:0;left:0;width:%(UI_W)dpx;height:%(UI_H)dpx;
  transform:scale(%(SCALE)f);transform-origin:top left;background:#fff;}
.punch{position:absolute;top:%(notchT)dpx;left:50%%;transform:translateX(-50%%);
  width:%(notchW)dpx;height:%(notchH)dpx;border-radius:%(notchR)dpx;background:#0B0B0C;z-index:60;}
.home-ind{position:absolute;bottom:%(hiB)dpx;left:50%%;transform:translateX(-50%%);
  width:%(hiW)dpx;height:%(hiH)dpx;border-radius:%(hiR)dpx;background:rgba(20,22,26,.32);z-index:60;}

/* ---------- floating callout cards ---------- */
.callout{position:absolute;z-index:40;display:flex;align-items:center;
  gap:%(coGap)dpx;width:%(coW)dpx;background:#fff;border-radius:%(coR)dpx;
  padding:%(coPad)dpx;box-shadow:0 %(coSy)dpx %(coSb)dpx rgba(30,10,8,.16);}
.co-ic{width:%(coI)dpx;height:%(coI)dpx;border-radius:50%%;background:#FFF1F0;
  flex:none;display:flex;align-items:center;justify-content:center;}
.co-t{font-size:%(coT)dpx;font-weight:800;letter-spacing:-0.015em;color:%(INK)s;
  line-height:1.2;}
.co-s{font-size:%(coS)dpx;font-weight:500;color:%(MUTED)s;line-height:1.3;
  margin-top:%(coSg)dpx;}

/* ---------- red brand card ---------- */
.card.red{background:%(RED)s;}
.card.red .ring{position:absolute;border-radius:50%%;background:rgba(0,0,0,.055);}
.logo-w{position:absolute;width:%(logoWr)dpx;filter:brightness(0) invert(1);}
.red h1{text-align:left;color:#fff;letter-spacing:-0.04em;margin:0;}
.red h1 b{color:#101012;font-weight:800;}
""" % dict(fonts=font_face_css(), W=b.w, H=b.h, CREAM=CREAM, PINK=PINK, PINK_LINE=PINK_LINE,
           INK=INK, RED=RED, MUTED=MUTED,
           blob=b.px(430), blobX=b.px(165), blobY=b.px(415),
           blobl=b.px(560), blobB=max(2, b.px(5)), bloblX=b.px(355), bloblY=b.px(165),
           headH=b.ph_top, logoW=b.px(172), logoGap=b.px(53),
           h1=b.px(99), h1Gap=b.px(28), sub=b.px(33), subPad=b.px(120),
           PH_W=b.ph_w, PH_H=b.ph_h, BEZEL=b.bezel, radius=b.radius, sradius=b.sradius,
           PH_LEFT=b.ph_left, PH_TOP=b.ph_top, SCR_W=b.scr_w, SCR_H=b.scr_h,
           UI_W=UI_W, UI_H=b.ui_h, SCALE=b.scale,
           notchT=b.notch_top, notchW=b.notch_w, notchH=b.notch_h, notchR=b.notch_r,
           hiB=b.hi_bottom, hiW=b.hi_w, hiH=b.hi_h, hiR=b.hi_h // 2,
           shadY=b.px(38), shadB=b.px(80), shad2=b.px(6), shad3=b.px(18),
           coGap=b.px(13), coW=b.px(358), coR=b.px(22), coPad=b.px(17),
           coSy=b.px(10), coSb=b.px(30), coI=b.px(47), coT=b.px(25),
           coS=b.px(20), coSg=b.px(2),
           logoWr=b.px(186))


def ui_css():
    """Styles for the in-phone UI (authored in the 390x868 logical space)."""
    return """
.ui{font-size:13px;color:%(TEXT)s;}
.statusbar{position:absolute;top:0;left:0;right:0;height:40px;z-index:50;
  display:flex;align-items:flex-end;justify-content:space-between;padding:0 22px 6px;
  font-size:12px;font-weight:700;color:%(TEXT)s;}
.statusbar .icons{display:flex;gap:5px;align-items:center;}
.bar{width:3px;background:%(TEXT)s;border-radius:1px;}
.batt{width:19px;height:10px;border:1.5px solid %(TEXT)s;border-radius:3px;padding:1.5px;}
.batt i{display:block;height:100%%;width:72%%;background:%(TEXT)s;border-radius:1px;}

/* map */
.map{position:absolute;inset:0;}
.marker{position:absolute;transform:translate(-50%%,-50%%);}
.pin{width:20px;height:20px;border-radius:50%%;border:4px solid #fff;
  box-shadow:0 2px 6px rgba(0,0,0,.28);}
.pin.start{background:%(SUCCESS)s;} .pin.end{background:%(RED)s;border-radius:5px;}
.carmark{width:34px;height:34px;border-radius:50%%;background:rgba(255,255,255,.55);
  display:flex;align-items:center;justify-content:center;}
.carmark svg{filter:drop-shadow(0 2px 5px rgba(0,0,0,.32));}

/* floating round map buttons */
.fab{position:absolute;width:38px;height:38px;border-radius:50%%;background:#fff;
  display:flex;align-items:center;justify-content:center;
  box-shadow:0 3px 10px rgba(0,0,0,.16);}

/* top bar (DriverTopBar) */
.topbar{position:absolute;top:46px;left:14px;right:14px;height:46px;z-index:40;
  display:flex;align-items:center;justify-content:space-between;}
.tb-pill{height:46px;display:flex;align-items:center;gap:9px;background:#fff;
  border-radius:23px;padding:0 15px 0 6px;box-shadow:0 3px 12px rgba(0,0,0,.13);}
.avatar{width:34px;height:34px;border-radius:50%%;background:%(RED)s;color:#fff;
  display:flex;align-items:center;justify-content:center;font-weight:800;font-size:14px;}
.tb-name{font-size:13px;font-weight:700;line-height:1.15;}
.tb-sub{font-size:10.5px;font-weight:600;color:%(MUTED)s;}
.tb-btns{display:flex;gap:8px;}
.tb-btn{width:38px;height:38px;border-radius:50%%;background:#fff;display:flex;
  align-items:center;justify-content:center;box-shadow:0 3px 12px rgba(0,0,0,.13);
  position:relative;}
.dot-badge{position:absolute;top:8px;right:9px;width:8px;height:8px;border-radius:50%%;
  background:%(RED)s;border:1.5px solid #fff;}

/* bottom sheets */
.sheet{position:absolute;left:0;right:0;bottom:0;background:#fff;
  border-radius:26px 26px 0 0;box-shadow:0 -6px 26px rgba(0,0,0,.13);padding:10px 18px 34px;}
.grab{width:38px;height:4px;border-radius:2px;background:#DDE0E4;margin:0 auto 12px;}
.pill{display:inline-flex;align-items:center;gap:6px;border-radius:999px;
  font-weight:700;font-size:11.5px;padding:6px 12px;}
.row{display:flex;align-items:center;}
.sp{justify-content:space-between;}
.btn{border-radius:14px;height:48px;display:flex;align-items:center;
  justify-content:center;font-weight:800;font-size:15px;}
.btn.primary{background:%(RED)s;color:#fff;box-shadow:0 6px 16px rgba(255,59,48,.32);}
.btn.ghost{background:#F1F2F4;color:%(TEXT)s;}
.muted{color:%(MUTED)s;} .bold{font-weight:800;}
""" % dict(TEXT=TEXT, MUTED=MUTED, RED=RED, SUCCESS=SUCCESS)


# Set per artboard by build_cards(); decides which platform chrome the mock-up
# status bar draws. Module-level so the scr_* builders stay parameter-free.
IOS_CHROME = False


def statusbar():
    bars = "".join('<div class="bar" style="height:%dpx"></div>' % h for h in (5, 8, 11, 14))
    wifi = ""
    if IOS_CHROME:
        wifi = ('<svg width="16" height="12" viewBox="0 0 16 12" fill="none" stroke="%s" '
                'stroke-width="1.7" stroke-linecap="round">'
                '<path d="M1.3 4.2a10 10 0 0 1 13.4 0"/>'
                '<path d="M3.7 6.8a6.4 6.4 0 0 1 8.6 0"/>'
                '<path d="M6.2 9.4a3 3 0 0 1 3.6 0"/></svg>' % TEXT)
    return ('<div class="statusbar"><div>9:41</div><div class="icons">%s%s'
            '<div class="batt"><i></i></div></div></div>' % (bars, wifi))


def map_svg(route=False, demand=False):
    """Warm-grey city map: block grid, river, highway, park."""
    p = ['<svg class="map" viewBox="0 0 390 868" xmlns="http://www.w3.org/2000/svg">',
         '<rect width="390" height="868" fill="#EAE7E2"/>']
    # building blocks
    blocks = [(20, 60, 70, 46), (104, 60, 62, 46), (250, 60, 58, 46), (322, 60, 70, 46),
              (20, 330, 70, 60), (104, 330, 62, 60), (250, 330, 58, 60), (322, 330, 70, 60),
              (20, 540, 70, 58), (104, 540, 62, 58), (188, 540, 50, 58), (272, 540, 62, 58),
              (20, 650, 70, 54), (104, 650, 62, 54), (188, 650, 50, 54), (272, 650, 62, 54),
              (20, 760, 70, 54), (104, 760, 62, 54), (188, 760, 50, 54), (272, 760, 62, 54)]
    for x, y, w, h in blocks:
        p.append('<rect x="%d" y="%d" width="%d" height="%d" rx="4" fill="#E2DED7"/>' % (x, y, w, h))
    p.append('<rect x="182" y="55" width="56" height="52" rx="7" fill="#CFE6C8"/>')
    for cx, cy in ((196, 70), (212, 88), (228, 68)):
        p.append('<circle cx="%d" cy="%d" r="3.4" fill="#BBD9B2"/>' % (cx, cy))
    # river + highway
    p.append('<path d="M-20 175 C 70 140, 150 225, 250 190 S 360 132, 410 168" '
             'stroke="#A9CCEA" stroke-width="44" fill="none" stroke-linecap="round"/>')
    p.append('<path d="M-20 470 C 110 452, 250 496, 410 462" '
             'stroke="#F2D07C" stroke-width="19" fill="none"/>')
    # street grid
    for x in (12, 96, 180, 264, 348):
        p.append('<line x1="%d" y1="-10" x2="%d" y2="878" stroke="#fff" stroke-width="12"/>' % (x, x))
    for y in (48, 300, 420, 528, 638, 748, 858):
        p.append('<line x1="-10" y1="%d" x2="400" y2="%d" stroke="#fff" stroke-width="12"/>' % (y, y))
    if demand:
        p.append('<defs><radialGradient id="dm"><stop offset="0%%" stop-color="%s" stop-opacity=".42"/>'
                 '<stop offset="60%%" stop-color="%s" stop-opacity=".18"/>'
                 '<stop offset="100%%" stop-color="%s" stop-opacity="0"/></radialGradient>'
                 '<radialGradient id="dm2"><stop offset="0%%" stop-color="%s" stop-opacity=".34"/>'
                 '<stop offset="100%%" stop-color="%s" stop-opacity="0"/></radialGradient></defs>'
                 % (RED, ORANGE, ORANGE, ORANGE, WARNING))
        p.append('<circle cx="255" cy="330" r="112" fill="url(#dm)"/>')
        p.append('<circle cx="92" cy="560" r="86" fill="url(#dm2)"/>')
    if route:
        p.append('<path d="M96 620 L96 470 L264 470 L264 256" stroke="#C2361F" '
                 'stroke-width="11" fill="none" stroke-linecap="round" stroke-linejoin="round"/>')
        p.append('<path d="M96 620 L96 470 L264 470 L264 256" stroke="%s" '
                 'stroke-width="7" fill="none" stroke-linecap="round" stroke-linejoin="round"/>' % RED)
    p.append('</svg>')
    return "".join(p)


ICONS = {
    "car": "M5.2 11.2l1.6-4.4A2.2 2.2 0 018.9 5.3h6.2a2.2 2.2 0 012.1 1.5l1.6 4.4v5.6h-2.4v-2H7.6v2H5.2z"
           "M7.8 13.6a1 1 0 100-2 1 1 0 000 2zm8.4 0a1 1 0 100-2 1 1 0 000 2z",
    "bell": "M12 2.8a5.2 5.2 0 00-5.2 5.2v3.1L5.2 14.4h13.6L17.2 11.1V8A5.2 5.2 0 0012 2.8z"
            "M9.9 16.6a2.1 2.1 0 004.2 0z",
    "flash": "M13.4 2L5.6 13.2h4.8L9.2 22l8.2-11.6h-5L13.4 2z",
    "star": "M12 2.6l2.7 5.6 6.1.8-4.5 4.3 1.2 6.1L12 16.4l-5.5 3 1.2-6.1L3.2 9l6.1-.8z",
    "flame": "M12.6 2c.4 3.2 3.4 4.6 3.4 8.2a4 4 0 11-8 0c0-1.8.9-2.9.9-2.9.2 1.9 1 2.6 1.6 2.6"
             " 1.3 0 2.1-2.2 2.1-7.9z",
    "target": "M12 4.4v2.2m0 10.8v2.2m7.6-7.6h-2.2M6.6 12H4.4",
    "check": "M4.8 12.4l4.6 4.6 9.8-9.8",
    "arrowturn": "M8.4 20.5V11a4.2 4.2 0 014.2-4.2h4.6M13.8 3.4l3.8 3.4-3.8 3.4",
    "shield": "M12 2.8l7 2.8v5.2c0 4.4-3 7.6-7 8.6-4-1-7-4.2-7-8.6V5.6z",
    "gift": "M4.4 10.6h15.2v9.2H4.4zM4.4 7.4h15.2v3.2H4.4zM12 7.4v12.4M12 7.4S10.6 4 8.8 4a2 2 0 000 3.4M12 7.4S13.4 4 15.2 4a2 2 0 010 3.4",
    "trophy": "M7 4.4h10v4.2a5 5 0 01-10 0zM7 5.6H4.4v1.6A2.6 2.6 0 007 9.8m10-4.2h2.6v1.6A2.6 2.6 0"
              " 0117 9.8M9.6 19.6h4.8M12 13.6v6",
    "cash": "M3.4 6.6h17.2v10.8H3.4zM12 14.6a2.6 2.6 0 100-5.2 2.6 2.6 0 000 5.2z",
    "mute": "M4.6 9.4h3.2L12 5.8v12.4l-4.2-3.6H4.6zM16 9.6l4 4.8M20 9.6l-4 4.8",
    "wav": "M12 4.6a1.6 1.6 0 100-3.2 1.6 1.6 0 000 3.2zM10 7v5h4.4l2.6 5M8.2 10.4a5.4 5.4 0 106.6 8",
    "pin": "M12 2.8a6.4 6.4 0 00-6.4 6.4c0 4.8 6.4 12 6.4 12s6.4-7.2 6.4-12A6.4 6.4 0 0012 2.8z",
    "chart": "M5 19V9.5M10.3 19V5M15.7 19v-8M21 19v-5",
    "phone": "M7.2 3.6h3l1.5 3.8-1.9 1.2a11 11 0 005.6 5.6l1.2-1.9 3.8 1.5v3a1.8 1.8 0 01-2 1.8A15.6 15.6 0 015.4 5.6a1.8 1.8 0 011.8-2z",
    "chat": "M20.4 12.4a7.6 7.6 0 01-8.4 7.6L6.6 21.4l1.4-4.2a7.6 7.6 0 1112.4-4.8z",
}


CAR_SVG = ('<svg width="24" height="32" viewBox="0 0 24 32">'
           '<rect x="1.6" y="0.6" width="20.8" height="30.8" rx="6.6" fill="#33383D"/>'
           '<path d="M4.6 8.6c0-2.3 2.7-4.4 7.4-4.4s7.4 2.1 7.4 4.4z" fill="#B3BAC3"/>'
           '<rect x="4.9" y="10.6" width="14.2" height="8.2" rx="2.2" fill="#565D66"/>'
           '<path d="M4.6 23.6c0 2.3 2.7 4 7.4 4s7.4-1.7 7.4-4z" fill="#98A1AB"/>'
           '<rect x="0" y="9.2" width="2.2" height="4" rx="1.1" fill="#33383D"/>'
           '<rect x="21.8" y="9.2" width="2.2" height="4" rx="1.1" fill="#33383D"/></svg>')


def icon(name, size=18, color="#1A1A1A", sw=1.9, fill=False):
    d = ICONS[name]
    style = ('fill="%s" stroke="none"' % color) if fill else \
            ('fill="none" stroke="%s" stroke-width="%s" stroke-linecap="round" '
             'stroke-linejoin="round"' % (color, sw))
    return ('<svg width="%d" height="%d" viewBox="0 0 24 24" %s><path d="%s"/></svg>'
            % (size, size, style, d))


def scr_dashboard(online=False, demand=False):
    """Dashboard + floating GO/STOP button (components/dashboard/DriverIdlePanel.tsx)."""
    if online:
        status = ('<div class="pill" style="background:#E7F8F1;color:%s">'
                  '<span style="width:8px;height:8px;border-radius:50%%;background:%s;'
                  'display:inline-block"></span>You\'re Online</div>' % (SUCCESS_DARK, SUCCESS))
        btn_bg = "linear-gradient(160deg,%s,%s)" % (SUCCESS_DARK, SUCCESS)
        glow = "rgba(16,185,129,.42)"
        label = "STOP"
    else:
        status = ('<div class="pill" style="background:#F1F2F4;color:%s">'
                  '<span style="width:8px;height:8px;border-radius:50%%;background:#9CA3AF;'
                  'display:inline-block"></span>You\'re Offline</div>' % MUTED)
        btn_bg = "linear-gradient(160deg,%s,%s)" % (RED, RED_DARK)
        glow = "rgba(255,59,48,.42)"
        label = "GO"
    return """
%(sb)s
%(map)s
<div class="marker" style="left:50%%;top:52%%"><div class="carmark">%(car)s</div></div>
<div class="topbar">
  <div class="tb-pill"><div class="avatar">S</div>
    <div><div class="tb-name">$184.60</div><div class="tb-sub">9 trips today</div></div></div>
  <div class="tb-btns">
    <div class="tb-btn">%(flash)s</div>
    <div class="tb-btn">%(bell)s<span class="dot-badge"></span></div></div>
</div>
<div class="fab" style="right:14px;top:170px">%(target)s</div>
<div class="fab" style="right:14px;top:220px">%(chart)s</div>
<div style="position:absolute;left:0;right:0;bottom:44px;display:flex;flex-direction:column;
     align-items:center;gap:11px;">
  <div class="pill" style="background:#fff;color:%(TEXT)s;box-shadow:0 4px 14px rgba(0,0,0,.14);
       padding:7px 8px 7px 13px;font-weight:700;">
    %(car2)s Grey Toyota Corolla
    <span style="background:#F1F2F4;border-radius:6px;padding:4px 8px;font-size:10.5px;
          letter-spacing:.06em;color:%(TEXT)s">4WX 812</span></div>
  %(status)s
  <div style="width:100px;height:100px;border-radius:50%%;background:%(btn_bg)s;
       display:flex;align-items:center;justify-content:center;
       box-shadow:0 10px 30px %(glow)s,0 0 0 8px rgba(255,255,255,.5);">
    <span style="color:#fff;font-size:28px;font-weight:800;letter-spacing:.02em">%(label)s</span></div>
</div>
""" % dict(sb=statusbar(), map=map_svg(demand=demand), TEXT=TEXT, MUTED=MUTED,
           flash=icon("flash", 19, TEXT, fill=True), bell=icon("bell", 19, TEXT),
           target=icon("target", 19, TEXT), car2=icon("car", 16, MUTED), car=CAR_SVG,
           chart=icon("chart", 19, TEXT),
           status=status, btn_bg=btn_bg, glow=glow, label=label)



def scr_demand():
    """Demand heatmap: busy zones with earnings context."""
    def zone(name, tag, tag_bg, tag_fg, per_trip, eta):
        return ("""<div class="row sp" style="padding:9px 0;border-top:1px solid %(BORDER)s">
  <div class="row" style="gap:10px">
    <div style="width:9px;height:9px;border-radius:50%%;background:%(tag_fg)s"></div>
    <div><div style="font-size:13.5px;font-weight:800">%(name)s</div>
      <div style="font-size:11px;font-weight:600;color:%(MUTED)s">%(eta)s away</div></div></div>
  <div class="row" style="gap:9px">
    <div style="font-size:13px;font-weight:800">%(per_trip)s</div>
    <div class="pill" style="background:%(tag_bg)s;color:%(tag_fg)s;padding:4px 9px">%(tag)s</div>
  </div></div>""" % dict(BORDER=BORDER, MUTED=MUTED, name=name, tag=tag, tag_bg=tag_bg,
                         tag_fg=tag_fg, per_trip=per_trip, eta=eta))
    fc = ""
    for hr, pct in [("Now", 88), ("5 PM", 96), ("6 PM", 72), ("7 PM", 54), ("8 PM", 63)]:
        fc += ('<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:6px">'
               '<div style="width:100%%;height:%dpx;border-radius:5px;background:%s"></div>'
               '<div style="font-size:9.5px;font-weight:700;color:%s">%s</div></div>'
               % (int(pct * 0.30), RED if pct > 85 else "#FFD5D1", MUTED, hr))
    return """
%(sb)s
%(map)s
<div class="marker" style="left:50%%;top:44%%"><div class="carmark">%(car)s</div></div>
<div class="topbar">
  <div class="tb-pill"><div class="avatar">S</div>
    <div><div class="tb-name">$184.60</div><div class="tb-sub">9 trips today</div></div></div>
  <div class="tb-btns"><div class="tb-btn">%(flash)s</div>
    <div class="tb-btn">%(bell)s<span class="dot-badge"></span></div></div>
</div>
<div style="position:absolute;top:104px;left:14px;right:14px;display:flex;gap:7px;">
  <div class="pill" style="background:#fff;color:%(TEXT)s;box-shadow:0 3px 10px rgba(0,0,0,.12)">
    %(flame)s Downtown &middot; 1.5&times;</div>
  <div class="pill" style="background:#fff;color:%(MUTED)s;box-shadow:0 3px 10px rgba(0,0,0,.12)">
    8th St E &middot; Busy</div>
</div>
<div class="sheet">
  <div class="grab"></div>
  <div class="row sp" style="margin-bottom:12px">
    <div style="font-size:17px;font-weight:800;letter-spacing:-0.02em">Where demand is now</div>
    <div class="pill" style="background:#FFF1F0;color:%(RED)s">Live</div></div>
  <div class="row" style="gap:9px;align-items:center;margin-bottom:14px">
    <div style="font-size:10px;font-weight:700;color:%(MUTED)s">LOW</div>
    <div style="flex:1;height:7px;border-radius:4px;
         background:linear-gradient(90deg,#FDE8C8,%(WARNING)s,%(RED)s)"></div>
    <div style="font-size:10px;font-weight:700;color:%(MUTED)s">HIGH</div></div>
  %(z1)s%(z2)s%(z3)s
  <div style="background:#F7F8F9;border-radius:16px;padding:11px 14px;margin-top:11px">
    <div class="row sp" style="margin-bottom:8px">
      <div style="font-size:11px;font-weight:800;letter-spacing:.09em;color:%(MUTED)s">
        NEXT FEW HOURS</div>
      <div style="font-size:11px;font-weight:700;color:%(MUTED)s">Busiest 5 PM</div></div>
    <div class="row" style="gap:7px;height:50px;align-items:flex-end">%(fc)s</div></div>
</div>
""" % dict(sb=statusbar(), map=map_svg(demand=True), TEXT=TEXT, MUTED=MUTED, RED=RED,
           WARNING=WARNING, fc=fc, car=CAR_SVG,
           flash=icon("flash", 19, TEXT, fill=True), bell=icon("bell", 19, TEXT),
           flame=icon("flame", 14, RED, fill=True),
           z1=zone("Downtown", "1.5&times;", "#FFF1F0", RED, "~$22/trip", "4 min"),
           z2=zone("University Heights", "Busy", "#FEF3C7", "#B45309", "~$17/trip", "9 min"),
           z3=zone("Stonebridge", "Steady", "#F1F2F4", MUTED, "~$14/trip", "12 min"))


def scr_offer():
    """Incoming ride offer (components/panels/RideOfferPanel.tsx)."""
    def badge(ic, text, bg, fg):
        return ('<div class="pill" style="background:%s;color:%s">%s%s</div>'
                % (bg, fg, ic, text))
    badges = "".join([
        badge(icon("flame", 13, "#B45309", fill=True), "1.5&times; surge", "#FEF3C7", "#B45309"),
        badge(icon("wav", 13, "#1D4ED8"), "WAV", "#DBEAFE", "#1D4ED8"),
        badge(icon("mute", 13, "#6D28D9"), "Quiet ride", "#EDE9FE", "#6D28D9"),
    ])
    return """
%(sb)s
%(map)s
<div style="position:absolute;inset:0;background:rgba(10,12,14,.14)"></div>
<div class="sheet" style="padding-top:0">
  <div style="height:5px;background:#F1F2F4;border-radius:3px;margin:0 -18px 14px;">
    <div style="height:5px;width:62%%;background:%(RED)s;border-radius:3px"></div></div>
  <div class="row sp" style="margin-bottom:14px">
    <div class="row" style="gap:10px">
      <div class="avatar" style="background:#F1F2F4;color:%(TEXT)s">M</div>
      <div><div style="font-size:15px;font-weight:800">Maya T.</div>
        <div class="row" style="gap:4px;font-size:11.5px;font-weight:700;color:%(MUTED)s">
          %(star)s 4.9 &middot; Standard Ride</div></div></div>
    <div style="width:52px;height:52px;border-radius:50%%;border:3.5px solid %(RED)s;
         display:flex;align-items:baseline;justify-content:center;gap:1px;padding-top:13px">
      <span style="font-size:20px;font-weight:800;color:%(RED)s">12</span>
      <span style="font-size:11px;font-weight:800;color:%(RED)s">s</span></div>
  </div>
  <div style="background:#F7F8F9;border-radius:18px;padding:14px 16px;margin-bottom:12px">
    <div class="row sp">
      <div style="font-size:10.5px;font-weight:800;letter-spacing:.1em;color:%(MUTED)s">YOUR EARNINGS</div>
      <div class="pill" style="background:#E7F8F1;color:%(SUCCESS_DARK)s;padding:4px 9px">
        100%% yours</div></div>
    <div class="row sp" style="align-items:flex-end;margin-top:2px">
      <div><div style="font-size:38px;font-weight:800;letter-spacing:-0.03em;line-height:1.1">$18.40</div>
        <div style="font-size:11.5px;font-weight:600;color:%(MUTED)s">$16.90 fare + $1.50 bonus</div></div>
      <div class="row" style="gap:16px;padding-bottom:6px">
        <div style="text-align:center"><div style="font-size:17px;font-weight:800">6.4</div>
          <div style="font-size:10px;font-weight:700;color:%(MUTED)s">km</div></div>
        <div style="text-align:center"><div style="font-size:17px;font-weight:800">14</div>
          <div style="font-size:10px;font-weight:700;color:%(MUTED)s">min</div></div>
        <div style="text-align:center"><div style="font-size:17px;font-weight:800">$2.87</div>
          <div style="font-size:10px;font-weight:700;color:%(MUTED)s">/km</div></div></div></div></div>
  <div class="row" style="gap:6px;margin-bottom:14px">%(badges)s</div>
  <div class="row" style="gap:11px;margin-bottom:11px">
    <div style="width:11px;height:11px;border-radius:50%%;background:%(SUCCESS)s;flex:none"></div>
    <div><div style="font-size:9.5px;font-weight:800;letter-spacing:.1em;color:%(MUTED)s">PICKUP</div>
      <div style="font-size:14px;font-weight:700">Broadway Ave &amp; 8th St E</div>
      <div style="font-size:11.5px;font-weight:600;color:%(MUTED)s">3 min away</div></div></div>
  <div class="row" style="gap:11px;margin-bottom:16px">
    <div style="width:11px;height:11px;border-radius:3px;background:%(RED)s;flex:none"></div>
    <div><div style="font-size:9.5px;font-weight:800;letter-spacing:.1em;color:%(MUTED)s">DROP-OFF</div>
      <div style="font-size:14px;font-weight:700">Midtown Plaza</div></div></div>
  <div class="row" style="gap:10px">
    <div class="btn ghost" style="width:112px">Decline</div>
    <div class="btn primary" style="flex:1">Accept &middot; $18.40</div></div>
</div>
""" % dict(sb=statusbar(), map=map_svg(), RED=RED, TEXT=TEXT, MUTED=MUTED, SUCCESS=SUCCESS,
           SUCCESS_DARK=SUCCESS_DARK, star=icon("star", 12, WARNING, fill=True), badges=badges)


def scr_nav():
    """Turn-by-turn to pickup (NavigationStepBanner + ActiveRidePanel)."""
    return """
%(sb)s
%(map)s
<div class="marker" style="left:24.6%%;top:62%%"><div class="carmark">%(car)s</div></div>
<div class="marker" style="left:67.7%%;top:28.8%%"><div class="pin start"></div></div>
<div style="position:absolute;top:44px;left:14px;right:14px;background:#fff;border-radius:20px;
     padding:14px 16px;box-shadow:0 6px 20px rgba(0,0,0,.16);display:flex;align-items:center;gap:13px">
  <div style="width:42px;height:42px;border-radius:13px;background:%(RED)s;display:flex;
       align-items:center;justify-content:center;flex:none">%(turn)s</div>
  <div style="flex:1"><div style="font-size:17px;font-weight:800;letter-spacing:-0.01em">Turn right onto</div>
    <div style="font-size:17px;font-weight:800;letter-spacing:-0.01em">Broadway Ave</div></div>
  <div style="text-align:right"><div style="font-size:19px;font-weight:800">300</div>
    <div style="font-size:11px;font-weight:700;color:%(MUTED)s">m</div></div>
</div>
<div class="fab" style="right:14px;top:150px">%(target)s</div>
<div class="sheet">
  <div class="grab"></div>
  <div class="row sp" style="margin-bottom:13px">
    <div class="pill" style="background:#FFF1F0;color:%(RED)s">En Route to Pickup</div>
    <div style="font-size:11.5px;font-weight:700;color:%(MUTED)s">3 min &middot; 1.2 km</div></div>
  <div class="row sp" style="margin-bottom:15px">
    <div class="row" style="gap:10px">
      <div class="avatar" style="background:#F1F2F4;color:%(TEXT)s">M</div>
      <div><div style="font-size:15px;font-weight:800">Maya T.</div>
        <div style="font-size:11.5px;font-weight:600;color:%(MUTED)s">Rider &middot; 4.9 &#9733;</div></div></div>
    <div class="row" style="gap:8px">
      <div class="fab" style="position:static;box-shadow:none;background:#F1F2F4">%(phone)s</div>
      <div class="fab" style="position:static;box-shadow:none;background:#F1F2F4">%(chat)s</div>
      <div class="fab" style="position:static;box-shadow:none;background:%(RED)s">%(sos)s</div>
      </div></div>
  <div style="background:#F7F8F9;border-radius:16px;padding:13px 15px;margin-bottom:14px">
    <div class="row" style="gap:11px;margin-bottom:10px">
      <div style="width:10px;height:10px;border-radius:50%%;background:%(SUCCESS)s;flex:none"></div>
      <div style="font-size:13.5px;font-weight:700">Broadway Ave &amp; 8th St E</div></div>
    <div class="row" style="gap:11px">
      <div style="width:10px;height:10px;border-radius:3px;background:%(RED)s;flex:none"></div>
      <div style="font-size:13.5px;font-weight:700;color:%(MUTED)s">Midtown Plaza</div></div></div>
  <div class="btn primary">I've Arrived at Pickup</div>
</div>
""" % dict(sb=statusbar(), map=map_svg(route=True), RED=RED, TEXT=TEXT, MUTED=MUTED, SUCCESS=SUCCESS,
           turn=icon("arrowturn", 22, "#fff"), target=icon("target", 19, TEXT),
           car=CAR_SVG, phone=icon("phone", 18, TEXT), chat=icon("chat", 18, TEXT),
           sos=icon("shield", 18, "#fff"))


def scr_pin():
    """4-digit rider PIN handshake (ActiveRidePanel verify step)."""
    boxes = ""
    for i in range(4):
        if i < 3:
            boxes += ('<div style="width:54px;height:64px;border-radius:16px;background:#F7F8F9;'
                      'border:2px solid %s;display:flex;align-items:center;justify-content:center">'
                      '<div style="width:13px;height:13px;border-radius:50%%;background:%s"></div></div>'
                      % (BORDER, TEXT))
        else:
            boxes += ('<div style="width:54px;height:64px;border-radius:16px;background:#fff;'
                      'border:2.5px solid %s;display:flex;align-items:center;justify-content:center">'
                      '<div style="width:2.5px;height:28px;background:%s;border-radius:2px"></div></div>'
                      % (RED, RED))
    keys = ""
    for k in ["1", "2", "3", "4", "5", "6", "7", "8", "9", "", "0", "<"]:
        if k == "":
            keys += "<div></div>"
        else:
            keys += ('<div style="height:44px;border-radius:14px;background:#F7F8F9;display:flex;'
                     'align-items:center;justify-content:center;font-size:20px;font-weight:700">%s</div>' % k)
    return """
%(sb)s
%(map)s
<div style="position:absolute;inset:0;background:rgba(10,12,14,.14)"></div>
<div class="sheet">
  <div class="grab"></div>
  <div class="row" style="gap:10px;margin-bottom:4px">
    <div style="width:34px;height:34px;border-radius:50%%;background:#FFF1F0;display:flex;
         align-items:center;justify-content:center">%(shield)s</div>
    <div style="font-size:19px;font-weight:800;letter-spacing:-0.02em">Verify Rider's PIN</div></div>
  <div style="font-size:13px;font-weight:600;color:%(MUTED)s;margin-bottom:18px;padding-left:44px">
    Ask rider for their 4-digit code</div>
  <div class="row" style="gap:10px;justify-content:center;margin-bottom:16px">%(boxes)s</div>
  <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-bottom:14px">%(keys)s</div>
  <div class="btn primary" style="margin-bottom:11px">Start Trip</div>
  <div style="text-align:center;font-size:13px;font-weight:700;color:%(MUTED)s">Start Without PIN</div>
</div>
""" % dict(sb=statusbar(), map=map_svg(), MUTED=MUTED, boxes=boxes, keys=keys,
           shield=icon("shield", 18, RED))


def scr_earnings():
    """Earnings / activity view (components/activity/ActivityView.tsx)."""
    tabs = ""
    for t in ("Today", "This Week", "This Month", "All Time"):
        on = t == "This Week"
        tabs += ('<div style="flex:1;text-align:center;padding:9px 0;border-radius:12px;font-size:12px;'
                 'font-weight:%s;background:%s;color:%s">%s</div>'
                 % ("800" if on else "700", "#fff" if on else "transparent",
                    TEXT if on else MUTED, t))
    bars = ""
    for lbl, pct in [("M", 42), ("T", 61), ("W", 38), ("T", 74), ("F", 96), ("S", 83), ("S", 27)]:
        bars += ('<div style="flex:1;display:flex;flex-direction:column;align-items:center;gap:7px">'
                 '<div style="width:100%%;height:%dpx;border-radius:7px 7px 3px 3px;background:%s"></div>'
                 '<div style="font-size:10px;font-weight:700;color:%s">%s</div></div>'
                 % (int(pct * 1.02), RED if pct > 90 else "#FFD5D1", MUTED, lbl))

    def trip(route, when, amt):
        return ("""<div class="row sp" style="padding:10px 0;border-bottom:1px solid %(BORDER)s">
  <div class="row" style="gap:10px"><div style="width:32px;height:32px;border-radius:10px;
    background:#F7F8F9;display:flex;align-items:center;justify-content:center">%(ic)s</div>
    <div><div style="font-size:12.5px;font-weight:700">%(route)s</div>
      <div style="font-size:10.5px;font-weight:600;color:%(MUTED)s">%(when)s</div></div></div>
  <div style="font-size:14px;font-weight:800">%(amt)s</div></div>"""
                % dict(BORDER=BORDER, MUTED=MUTED, route=route, when=when, amt=amt,
                       ic=icon("pin", 15, MUTED)))

    def brk(ic, label, val):
        return ('<div style="flex:1;background:#F7F8F9;border-radius:15px;padding:12px">'
                '%s<div style="font-size:10px;font-weight:700;color:%s;margin-top:6px">%s</div>'
                '<div style="font-size:15px;font-weight:800;margin-top:1px">%s</div></div>'
                % (ic, MUTED, label, val))
    return """
%(sb)s
<div style="position:absolute;inset:0;background:#fff;padding:52px 18px 0">
  <div class="row sp" style="margin-bottom:16px">
    <div style="font-size:26px;font-weight:800;letter-spacing:-0.03em">Earnings</div>
    <div class="fab" style="position:static;background:#F7F8F9;box-shadow:none">%(cash)s</div></div>
  <div class="row" style="background:#F1F2F4;border-radius:15px;padding:3px;margin-bottom:18px">%(tabs)s</div>
  <div style="font-size:11px;font-weight:800;letter-spacing:.1em;color:%(MUTED)s">TOTAL EARNED</div>
  <div class="row" style="align-items:baseline;gap:9px;margin-bottom:3px">
    <div style="font-size:48px;font-weight:800;letter-spacing:-0.04em">$742.18</div>
    <div class="pill" style="background:#E7F8F1;color:%(SUCCESS_DARK)s;padding:4px 9px">+12%%</div></div>
  <div style="font-size:12.5px;font-weight:600;color:%(MUTED)s;margin-bottom:18px">
    38 trips &middot; Sep 8 &ndash; Sep 14</div>
  <div class="row" style="gap:8px;height:118px;align-items:flex-end;margin-bottom:22px">%(bars)s</div>
  <div class="row" style="gap:9px;margin-bottom:9px">%(b1)s%(b2)s</div>
  <div class="row" style="gap:9px;margin-bottom:18px">%(b3)s%(b4)s</div>
  <div style="background:#F7F8F9;border-radius:18px;padding:15px 16px">
    <div class="row sp" style="margin-bottom:11px">
      <div><div style="font-size:10px;font-weight:800;letter-spacing:.1em;color:%(MUTED)s">
        AVAILABLE BALANCE</div>
        <div style="font-size:23px;font-weight:800;letter-spacing:-0.02em">$318.64</div></div></div>
    <div class="row sp" style="border-top:1px solid %(BORDER)s;padding-top:11px">
      <div style="font-size:12px;font-weight:700;color:%(MUTED)s">Next payout</div>
      <div style="font-size:12px;font-weight:800">Every Sunday</div></div></div>
  <div style="font-size:13.5px;font-weight:800;margin:17px 0 4px">Recent trips</div>
  %(t1)s%(t2)s
</div>
""" % dict(sb=statusbar(), tabs=tabs, bars=bars, MUTED=MUTED, BORDER=BORDER,
           t1=trip("Broadway Ave &rarr; Midtown Plaza", "Today, 4:12 PM", "$18.40"),
           t2=trip("8th St E &rarr; Sutherland", "Today, 3:26 PM", "$12.75"),
           SUCCESS_DARK=SUCCESS_DARK, cash=icon("cash", 18, TEXT),
           b1=brk(icon("cash", 16, MUTED), "Fare", "$648.20"),
           b2=brk(icon("gift", 16, MUTED), "Tips", "$54.00"),
           b3=brk(icon("flash", 16, MUTED, fill=True), "Bonus", "$39.98"),
           b4=brk(icon("chart", 16, MUTED), "Total Trips", "38"))


def scr_quests():
    """Quests & bonuses (app/driver/quests.tsx)."""
    def quest(title, sub, cur, goal, reward, pct, done=False):
        return """
<div style="background:#fff;border:1.5px solid %(BORDER)s;border-radius:20px;padding:15px;
     margin-bottom:11px">
  <div class="row sp" style="margin-bottom:9px">
    <div class="row" style="gap:10px">
      <div style="width:38px;height:38px;border-radius:12px;background:%(ibg)s;display:flex;
           align-items:center;justify-content:center">%(ic)s</div>
      <div><div style="font-size:14.5px;font-weight:800;letter-spacing:-0.01em">%(title)s</div>
        <div style="font-size:11.5px;font-weight:600;color:%(MUTED)s">%(sub)s</div></div></div>
    <div class="pill" style="background:#E7F8F1;color:%(SUCCESS_DARK)s">+$%(reward)s</div></div>
  <div style="height:8px;border-radius:4px;background:#F1F2F4;overflow:hidden;margin-bottom:7px">
    <div style="height:8px;width:%(pct)d%%;border-radius:4px;background:%(fill)s"></div></div>
  <div class="row sp"><div style="font-size:11.5px;font-weight:700;color:%(MUTED)s">%(cur)s of %(goal)s trips</div>
    <div style="font-size:11.5px;font-weight:800;color:%(fill)s">%(pct)d%%</div></div>
</div>""" % dict(BORDER=BORDER, MUTED=MUTED, SUCCESS_DARK=SUCCESS_DARK, title=title, sub=sub,
                 reward=reward, pct=pct, cur=cur, goal=goal,
                 fill=SUCCESS if done else RED,
                 ibg="#E7F8F1" if done else "#FFF1F0",
                 ic=icon("trophy" if done else "flag" if False else "trophy", 19,
                         SUCCESS_DARK if done else RED))
    tabs = ""
    for t in ("Active", "To claim", "Earned"):
        on = t == "Active"
        tabs += ('<div style="flex:1;text-align:center;padding:9px 0;border-radius:12px;font-size:12px;'
                 'font-weight:%s;background:%s;color:%s">%s</div>'
                 % ("800" if on else "700", "#fff" if on else "transparent",
                    TEXT if on else MUTED, t))
    return """
%(sb)s
<div style="position:absolute;inset:0;background:#fff;padding:52px 18px 0">
  <div style="font-size:26px;font-weight:800;letter-spacing:-0.03em;margin-bottom:4px">
    Quests &amp; Bonuses</div>
  <div style="font-size:13px;font-weight:600;color:%(MUTED)s;margin-bottom:16px">
    Hit the target, keep the bonus &mdash; on top of your fares</div>
  <div class="row" style="background:#F1F2F4;border-radius:15px;padding:3px;margin-bottom:18px">%(tabs)s</div>
  <div style="background:linear-gradient(135deg,%(RED)s,%(ORANGE)s);border-radius:20px;padding:16px;
       margin-bottom:16px;color:#fff">
    <div class="row sp"><div>
      <div style="font-size:10.5px;font-weight:800;letter-spacing:.1em;opacity:.9">EARNED THIS WEEK</div>
      <div style="font-size:32px;font-weight:800;letter-spacing:-0.03em">$65.00</div></div>
      <div style="width:46px;height:46px;border-radius:50%%;background:rgba(255,255,255,.22);
           display:flex;align-items:center;justify-content:center">%(trophy)s</div></div></div>
  %(q1)s%(q2)s%(q3)s%(q4)s
  <div style="background:#F7F8F9;border-radius:20px;padding:14px;display:flex;
       align-items:center;gap:12px">
    <div style="width:40px;height:40px;border-radius:13px;background:#FFF7E6;display:flex;
         align-items:center;justify-content:center">%(gift)s</div>
    <div style="flex:1"><div style="font-size:14px;font-weight:800">Loyalty points</div>
      <div style="font-size:11.5px;font-weight:600;color:%(MUTED)s">
        2,480 pts &middot; fuel &amp; accessory discounts</div></div>
    <div style="font-size:13px;font-weight:800;color:%(RED)s">Redeem</div></div>
</div>
""" % dict(sb=statusbar(), tabs=tabs, MUTED=MUTED, RED=RED, ORANGE=ORANGE,
           trophy=icon("trophy", 24, "#fff"), gift=icon("gift", 19, "#B45309"),
           q1=quest("Weekly 20", "Ends Sunday 11:59 PM", 14, 20, "40.00", 70),
           q2=quest("Morning rush", "5 trips before 9 AM", 3, 5, "15.00", 60),
           q3=quest("Airport runs", "3 pickups from YXE", 1, 3, "18.00", 33),
           q4=quest("Weekend streak", "Completed Saturday", 12, 12, "25.00", 100, done=True))


ICONS["leaf"] = ("M12 2.5l1.9 3.6c.2.4.6.3.9.1l1.4-.7-.9 4.3c-.2.8.3 1 .8.5l2.5-2.6.6 1.4c.1.3.4.4.7.3"
                 "l2.6-.5-.9 3.1c-.1.4 0 .6.3.8l1.1.5-4.6 3.7c-.4.3-.3.6-.2 1l.4 1.4-4.5-.5c-.3 0-.6.2-.6.5"
                 "l.2 4.1h-1.4l.2-4.1c0-.3-.3-.5-.6-.5l-4.5.5.4-1.4c.1-.4.2-.7-.2-1L2.9 15.4l1.1-.5"
                 "c.3-.2.4-.4.3-.8l-.9-3.1 2.6.5c.3.1.6 0 .7-.3l.6-1.4 2.5 2.6c.5.5 1 .3.8-.5l-.9-4.3"
                 "1.4.7c.3.2.7.3.9-.1L12 2.5z")


def phone(b, ui, cls="center", style=""):
    return ('<div class="phone %s" style="%s"><div class="screen"><div class="punch"></div>'
            '<div class="ui">%s</div><div class="home-ind"></div></div></div>'
            % (cls, style, ui))


def callout(b, side, y_frac, ic, title, sub):
    """Floating feature card overlapping the phone, as on the rider-app set."""
    edge = "left" if side == "left" else "right"
    return ('<div class="callout" style="%s:%dpx;top:%dpx">'
            '<div class="co-ic">%s</div>'
            '<div><div class="co-t">%s</div><div class="co-s">%s</div></div></div>'
            % (edge, b.px(37), round(y_frac * b.h), ic, title, sub))


def light_card(b, headline, sub, ui, logo, callouts=()):
    cards = "".join(callout(b, *c) for c in callouts)
    return ('<div class="card light"><div class="blob-r"></div><div class="blob-l"></div>'
            '<div class="head"><img class="logo" src="%s"><h1>%s</h1>'
            '<div class="sub">%s</div></div>%s%s</div>'
            % (logo, headline, sub, phone(b, ui), cards))


def red_hero_card(b, ui, logo):
    """Opening brand card: the 0%% commission promise + an angled driver phone."""
    return """
<div class="card red">
  <div class="ring" style="width:%(ring1)dpx;height:%(ring1)dpx;right:-%(ring1X)dpx;
       top:-%(ring1Y)dpx"></div>
  <div class="ring" style="width:%(ring2)dpx;height:%(ring2)dpx;left:-%(ring2X)dpx;
       bottom:-%(ring2Y)dpx"></div>
  <img class="logo-w" src="%(logo)s" style="left:%(padX)dpx;top:%(padY)dpx;width:%(logoW)dpx">
  <div style="position:absolute;left:%(padX)dpx;top:%(h1Top)dpx;">
    <h1 style="font-size:%(h1)dpx;line-height:0.97">Drive<br><b>Canadian.</b><br>Keep 100%%.</h1>
    <div style="display:flex;align-items:center;gap:%(leafGap)dpx;margin-top:%(leafTop)dpx">
      %(leaf)s<span style="color:#fff;font-size:%(leafTx)dpx;font-weight:800;
      letter-spacing:-0.01em">Your fare stays yours.</span></div>
  </div>
  <div style="position:absolute;left:%(padX)dpx;bottom:%(claimY)dpx">
    <div style="color:#fff;font-size:%(claim)dpx;font-weight:800;letter-spacing:-0.035em">
      0%% commission.</div>
    <div style="color:rgba(255,255,255,.92);font-size:%(claimSub)dpx;font-weight:500;
         margin-top:%(claimGap)dpx">You keep 100%% of every fare you drive.</div>
  </div>
  %(phone)s
</div>
""" % dict(logo=logo, leaf=icon("leaf", b.px(30), "#fff", fill=True),
           ring1=b.px(600), ring1X=b.px(200), ring1Y=b.px(120),
           ring2=b.px(420), ring2X=b.px(150), ring2Y=b.px(140),
           padX=b.px(74), padY=b.px(74), logoW=b.px(168),
           h1Top=b.px(196), h1=b.px(96), leafGap=b.px(12), leafTop=b.px(34),
           leafTx=b.px(30), claimY=b.px(96), claim=b.px(46), claimSub=b.px(26),
           claimGap=b.px(8),
           phone=phone(b, ui, "", "left:%dpx;top:%dpx;transform:rotate(-12deg);"
                       % (round(0.6537 * b.w), round(0.2448 * b.h))))


def page(b, card, body_bg):
    return ("<!doctype html><html><head><meta charset='utf-8'><style>%s\n%s\n"
            "html,body{background:%s;}</style></head><body>%s</body></html>"
            % (css(b), ui_css(), body_bg, card))


def viewport_inset(chrome, build_dir):
    """Headless Chromium reserves window chrome, so the viewport is shorter than
    --window-size. Capturing then crops the page and back-fills the remainder with
    the body colour, silently cutting the bottom of every artboard. Measure the
    inset once and add it back."""
    probe = build_dir / "_probe.html"
    probe.write_text("<html><head><script>window.addEventListener('load',function(){"
                     "document.title='VH'+innerHeight;});</script></head><body></body></html>")
    out = subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu",
                          "--hide-scrollbars", "--force-device-scale-factor=1",
                          "--virtual-time-budget=800", "--window-size=%d,%d" % (REF_W, REF_H),
                          "--dump-dom", str(probe)], capture_output=True, text=True).stdout
    probe.unlink(missing_ok=True)
    # match the rendered <title>, not the "VH" literal inside the probe script
    marker = out.find("<title>VH")
    if marker == -1:
        return 0
    digits = ""
    for ch in out[marker + len("<title>VH"):]:
        if not ch.isdigit():
            break
        digits += ch
    inset = REF_H - int(digits) if digits else 0
    if inset:
        print("  viewport inset: %dpx (compensating)" % inset)
    return max(0, inset)


def png_size(path):
    d = pathlib.Path(path).read_bytes()
    return struct.unpack(">II", d[16:24])


def _png_decode(path):
    d = pathlib.Path(path).read_bytes()
    pos, idat, w, h, nch = 8, b"", 0, 0, 3
    while pos < len(d):
        ln = struct.unpack(">I", d[pos:pos + 4])[0]
        typ, body = d[pos + 4:pos + 8], d[pos + 8:pos + 8 + ln]
        if typ == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            if depth != 8 or ctype not in (2, 6):
                raise SystemExit("unsupported PNG (depth %d, colour type %d)" % (depth, ctype))
            nch = 3 if ctype == 2 else 4
        elif typ == b"IDAT":
            idat += body
        elif typ == b"IEND":
            break
        pos += 12 + ln
    raw = zlib.decompress(idat)
    stride, rows, prev, i = w * nch, [], bytearray(w * nch), 0
    for _ in range(h):
        f = raw[i]
        line = bytearray(raw[i + 1:i + 1 + stride])
        i += 1 + stride
        if f == 2:                                   # Up -- the common case, keep it cheap
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 255
        elif f == 1:                                 # Sub
            for x in range(nch, stride):
                line[x] = (line[x] + line[x - nch]) & 255
        elif f == 3:                                 # Average
            for x in range(stride):
                a = line[x - nch] if x >= nch else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 255
        elif f == 4:                                 # Paeth
            for x in range(stride):
                a = line[x - nch] if x >= nch else 0
                bb = prev[x]
                c = prev[x - nch] if x >= nch else 0
                pp = a + bb - c
                pa, pb, pc = abs(pp - a), abs(pp - bb), abs(pp - c)
                line[x] = (line[x] + (a if (pa <= pb and pa <= pc) else
                                      (bb if pb <= pc else c))) & 255
        rows.append(bytes(line))
        prev = line
    return w, h, nch, rows


def _png_encode(path, w, h, nch, rows):
    def chunk(typ, body):
        return (struct.pack(">I", len(body)) + typ + body
                + struct.pack(">I", zlib.crc32(typ + body) & 0xFFFFFFFF))
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 2 if nch == 3 else 6, 0, 0, 0)
    data = zlib.compress(b"".join(b"\x00" + r for r in rows), 9)
    pathlib.Path(path).write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                                  + chunk(b"IDAT", data) + chunk(b"IEND", b""))


def crop_to(path, w, h):
    """Trim the viewport-inset padding Chromium appends below the artboard."""
    gw, gh = png_size(path)
    if (gw, gh) == (w, h):
        return
    dw, dh, nch, rows = _png_decode(path)
    _png_encode(path, w, h, nch, rows[:h])


def build_cards(b, logo):
    """The listing set, in running order. 01-06 are the core six; 07-08 are
    optional extras (Play Store allows 8 phone screenshots)."""
    global IOS_CHROME
    IOS_CHROME = b.notch == "island"

    def ic(name, colour=RED):
        return icon(name, b.px(23), colour)

    # (side, vertical position as a fraction of canvas height, icon, title, sub)
    # Positions mirror the rider-app set: upper-left and lower-right, each
    # overlapping the phone.
    return [
        ("01-drive-canadian", red_hero_card(b, scr_earnings(), logo), RED),
        ("02-go-online", light_card(
            b, "Go online.<br>Earn on your <em>terms</em>",
            "One tap to go live &mdash; and today's earnings and trip count stay in view "
            "the whole time you drive.",
            scr_dashboard(online=False), logo, [
                ("left", 0.40, ic("flash"), "One tap online", "Go live instantly"),
                ("right", 0.70, ic("cash"), "Today's earnings", "Always in view"),
            ]), CREAM),
        ("03-know-your-earnings", light_card(
            b, "Know what<br>you'll <em>earn</em>",
            "Every offer shows the full payout, both stops and the distance before you accept "
            "&mdash; and all of it is yours.",
            scr_offer(), logo, [
                ("left", 0.34, ic("cash"), "Full payout upfront", "Before you accept"),
                ("right", 0.83, ic("check", SUCCESS_DARK), "100% yours", "Spinr takes 0%"),
            ]), CREAM),
        ("04-demand", light_card(
            b, "Drive where<br>the <em>demand</em> is",
            "A live map of the busy zones, what each one is paying per trip, "
            "and when the next rush lands.",
            scr_demand(), logo, [
                ("left", 0.45, ic("flame"), "Live heat map", "See the busy zones"),
                ("right", 0.61, ic("chart"), "Paying per trip", "Know where to go"),
            ]), CREAM),
        ("05-guided-live", light_card(
            b, "Every trip,<br>guided <em>live</em>",
            "Turn-by-turn to the pickup, the rider one tap away, "
            "and safety controls on the same screen.",
            scr_nav(), logo, [
                ("left", 0.40, ic("pin"), "Turn-by-turn", "Straight to pickup"),
                ("right", 0.71, ic("shield"), "Safety built in", "SOS one tap away"),
            ]), CREAM),
        ("06-earnings", light_card(
            b, "Your earnings.<br><em>Clearly.</em>",
            "Today, this week, this month &mdash; total earned, trips and your next payout, "
            "always in view.",
            scr_earnings(), logo, [
                ("left", 0.52, ic("cash"), "Paid every Sunday", "Automatic weekly deposits"),
                ("right", 0.81, ic("chart"), "T4A-ready", "Tax docs at year end"),
            ]), CREAM),
        ("07-rider-pin", light_card(
            b, "The right rider,<br>every <em>time</em>",
            "A 4-digit PIN handshake confirms who's getting in before the trip starts.",
            scr_pin(), logo, [
                ("left", 0.36, ic("shield"), "PIN handshake", "Right rider, every time"),
                ("right", 0.82, ic("check", SUCCESS_DARK), "Verified pickup", "Before the trip starts"),
            ]), CREAM),
        ("08-quests", light_card(
            b, "Hit targets,<br>keep the <em>bonus</em>",
            "Quest challenges pay out on top of your fares, and land straight in your wallet.",
            scr_quests(), logo, [
                ("left", 0.55, ic("trophy"), "Bonus on top", "Of every fare you drive"),
                ("right", 0.81, ic("cash"), "Straight to wallet", "The moment you hit it"),
            ]), CREAM),
    ]


def main():
    chrome = find_chrome()
    only = sys.argv[1] if len(sys.argv) > 1 else None
    OUT.mkdir(parents=True, exist_ok=True)
    # Private scratch dir per run, so two artboard sizes can render concurrently
    # without one deleting the other's staged HTML.
    build = pathlib.Path(tempfile.mkdtemp(prefix="spinr-store-shots-"))
    logo = data_uri(APP / "assets" / "images" / "spinr-logo.png", "image/png")
    inset = viewport_inset(chrome, build)

    boards = [Board(*a) for a in ARTBOARDS if only is None or a[0] == only]
    if not boards:
        sys.exit("No artboard named %r. Known: %s"
                 % (only, ", ".join(a[0] for a in ARTBOARDS)))

    total = 0
    for b in boards:
        cards = build_cards(b, logo)
        print("%s -- %d artboards at %dx%d" % (b.key, len(cards), b.w, b.h))
        for name, html, bg in cards:
            src = build / ("%s-%s.html" % (b.key, name))
            src.write_text(page(b, html, bg), encoding="utf-8")
            dest = OUT / ("%s-%s.png" % (b.key, name))
            subprocess.run([
                chrome, "--headless", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
                "--force-device-scale-factor=1", "--virtual-time-budget=2500",
                "--window-size=%d,%d" % (b.w, b.h + inset),
                "--screenshot=%s" % dest, str(src),
            ], check=True, capture_output=True)
            crop_to(dest, b.w, b.h)
            got = png_size(dest)
            if got != (b.w, b.h):
                sys.exit("%s rendered at %dx%d, expected %dx%d"
                         % ((dest.name,) + got + (b.w, b.h)))
            print("  %s" % dest.name)
            total += 1
    shutil.rmtree(build, ignore_errors=True)
    print("\n%d screenshots -> %s" % (total, OUT))


if __name__ == "__main__":
    main()
