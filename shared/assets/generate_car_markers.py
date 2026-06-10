#!/usr/bin/env python3
"""Generate the top-down car marker PNGs used on the live maps.

Three variants, one per vehicle class — clean modern-EV top view (smooth
white body, very rounded hood, dark glass, full-width tail light blade
flush with the body, no wheel/grille detail), nose pointing up,
transparent background:

  - sedan : white sedan             -> car_marker_sedan.png
  - suv   : white long-roof SUV     -> car_marker_suv.png
  - lux   : black luxury sedan      -> car_marker_lux.png

Canvas is 120x220 for every variant so `CarMarker`'s `size` prop scales
them identically; the SUV reads bigger via a wider body and a long flat
roof within the same canvas. PNGs are rasterized at 3x (360x660) so they
stay crisp at marker sizes up to ~64 px.

Usage:  pip install cairosvg && python3 shared/assets/generate_car_markers.py
Output is written next to this script.
"""

import re
from pathlib import Path

import cairosvg

OUT_DIR = Path(__file__).resolve().parent
SCALE = 3  # 120x220 viewBox -> 360x660 png


def _expand_hex8(svg: str) -> str:
    """Rewrite fill='#RRGGBBAA' as fill + fill-opacity.

    cairosvg silently drops the alpha byte of 8-digit hex colors (renders
    them fully opaque), so translucent shadows/glare must go through the
    explicit *-opacity attributes.
    """

    def repl(m: "re.Match[str]") -> str:
        rgb, alpha = m.group(1), int(m.group(2), 16)
        return f"fill='#{rgb}' fill-opacity='{alpha / 255:.3f}'"

    return re.sub(r"fill='#([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})'", repl, svg)


SVG_HEADER = """<svg xmlns='http://www.w3.org/2000/svg' width='120' height='220' viewBox='0 0 120 220'>"""


def _shadow_defs(prefix: str) -> str:
    """Soft radial drop shadow (cairosvg has no feGaussianBlur)."""
    return f"""
    <radialGradient id='{prefix}shadow' cx='0.5' cy='0.5' r='0.5'>
      <stop offset='0%' stop-color='#000000' stop-opacity='0.26'/>
      <stop offset='72%' stop-color='#000000' stop-opacity='0.22'/>
      <stop offset='100%' stop-color='#000000' stop-opacity='0'/>
    </radialGradient>"""


def _white_body_defs(prefix: str) -> str:
    return f"""
    <linearGradient id='{prefix}body' x1='0' y1='0' x2='1' y2='0'>
      <stop offset='0%' stop-color='#E4E4E8'/>
      <stop offset='18%' stop-color='#F6F6F8'/>
      <stop offset='50%' stop-color='#FFFFFF'/>
      <stop offset='82%' stop-color='#F6F6F8'/>
      <stop offset='100%' stop-color='#E4E4E8'/>
    </linearGradient>
    <linearGradient id='{prefix}ws' x1='0' y1='0' x2='0' y2='1'>
      <stop offset='0%' stop-color='#23232B'/>
      <stop offset='100%' stop-color='#101016'/>
    </linearGradient>"""


SEDAN_SVG = (
    SVG_HEADER
    + "<defs>"
    + _shadow_defs("s")
    + _white_body_defs("s")
    + """
  </defs>

  <!-- Soft ground shadow -->
  <ellipse cx='60' cy='112' rx='49' ry='110' fill='url(#sshadow)'/>

  <!-- Side mirrors (under the body shoulder) -->
  <rect x='14' y='92' width='13' height='6.5' rx='3.25' fill='#F2F2F5' stroke='#CFCFD4' stroke-width='0.8' transform='rotate(-20 20.5 95)'/>
  <rect x='93' y='92' width='13' height='6.5' rx='3.25' fill='#F2F2F5' stroke='#CFCFD4' stroke-width='0.8' transform='rotate(20 99.5 95)'/>

  <!-- Body: smooth capsule, very rounded hood -->
  <path d='M 60 8
           C 40 8 27 21 26 47
           C 25.4 60 25 74 25 92
           L 25 174
           C 25 197 35 211 60 211
           C 85 211 95 197 95 174
           L 95 92
           C 95 74 94.6 60 94 47
           C 93 21 80 8 60 8 Z'
        fill='url(#sbody)' stroke='#D2D2D7' stroke-width='1'/>

  <!-- Hood seam -->
  <path d='M 36 33 Q 60 28 84 33' fill='none' stroke='#E6E6EA' stroke-width='1.1'/>

  <!-- Windshield: dark glass -->
  <path d='M 38 52
           C 46 50 74 50 82 52
           C 87 53 88.6 55.5 89 60
           L 91 89
           Q 91.6 98 83 98
           L 37 98
           Q 28.4 98 29 89
           L 31 60
           C 31.4 55.5 33 53 38 52 Z'
        fill='url(#sws)'/>

  <!-- Side window slivers -->
  <path d='M 28.5 103 Q 26.8 126 28.5 149 Q 30.5 152 32.5 149 Q 31.2 126 32.5 103 Q 30.5 100 28.5 103 Z' fill='#111116'/>
  <path d='M 91.5 103 Q 93.2 126 91.5 149 Q 89.5 152 87.5 149 Q 88.8 126 87.5 103 Q 89.5 100 91.5 103 Z' fill='#111116'/>

  <!-- Roof seams -->
  <path d='M 34 104 Q 60 101 86 104' fill='none' stroke='#E2E2E6' stroke-width='1'/>
  <path d='M 34 149 Q 60 152 86 149' fill='none' stroke='#E2E2E6' stroke-width='1'/>

  <!-- Rear window -->
  <path d='M 37 155
           L 83 155
           Q 88 155 88 161
           L 86.5 177
           Q 86 183 80 183
           L 40 183
           Q 34 183 33.5 177
           L 32 161
           Q 32 155 37 155 Z'
        fill='#0F0F13'/>

  <!-- Trunk seam -->
  <path d='M 36 192 Q 60 196 84 192' fill='none' stroke='#E6E6EA' stroke-width='1.1'/>

  <!-- Full-width tail light blade (Tesla/Polestar style), flush with body -->
  <path d='M 33 202 Q 60 207.5 87 202' fill='none' stroke='#E0473D' stroke-width='3.4' stroke-linecap='round'/>
</svg>"""
)


# SUV: wider body, short hood, long flat roof with longitudinal seams,
# thin wrap-around tailgate glass.
SUV_SVG = (
    SVG_HEADER
    + "<defs>"
    + _shadow_defs("u")
    + _white_body_defs("u")
    + """
  </defs>

  <!-- Soft ground shadow -->
  <ellipse cx='60' cy='112' rx='55' ry='112' fill='url(#ushadow)'/>

  <!-- Side mirrors -->
  <rect x='8' y='86' width='14' height='7' rx='3.5' fill='#F2F2F5' stroke='#CFCFD4' stroke-width='0.8' transform='rotate(-20 15 89.5)'/>
  <rect x='98' y='86' width='14' height='7' rx='3.5' fill='#F2F2F5' stroke='#CFCFD4' stroke-width='0.8' transform='rotate(20 105 89.5)'/>

  <!-- Body: wider, squarer tail -->
  <path d='M 60 8
           C 37 8 22 20 20.8 44
           C 20.2 56 20 70 20 88
           L 20 180
           C 20 202 30 212 60 212
           C 90 212 100 202 100 180
           L 100 88
           C 100 70 99.8 56 99.2 44
           C 98 20 83 8 60 8 Z'
        fill='url(#ubody)' stroke='#CECED3' stroke-width='1.1'/>

  <!-- Hood seam (short hood) -->
  <path d='M 32 30 Q 60 25 88 30' fill='none' stroke='#E6E6EA' stroke-width='1.1'/>

  <!-- Windshield: upright, wide -->
  <path d='M 34 46
           C 43 44 77 44 86 46
           C 91 47 92.6 49.5 93 54
           L 95 84
           Q 95.6 93 87 93
           L 33 93
           Q 24.4 93 25 84
           L 27 54
           C 27.4 49.5 29 47 34 46 Z'
        fill='url(#uws)'/>

  <!-- Side window slivers: run the full long cabin -->
  <path d='M 23.5 98 Q 21.6 132 23.5 166 Q 25.5 169 27.5 166 Q 26 132 27.5 98 Q 25.5 95 23.5 98 Z' fill='#111116'/>
  <path d='M 96.5 98 Q 98.4 132 96.5 166 Q 94.5 169 92.5 166 Q 94 132 92.5 98 Q 94.5 95 96.5 98 Z' fill='#111116'/>

  <!-- Long roof: longitudinal seams (the SUV tell from above) -->
  <path d='M 29 99 Q 60 96 91 99' fill='none' stroke='#E2E2E6' stroke-width='1'/>
  <path d='M 43 104 L 43 162' fill='none' stroke='#E4E4E8' stroke-width='1.1'/>
  <path d='M 77 104 L 77 162' fill='none' stroke='#E4E4E8' stroke-width='1.1'/>
  <path d='M 29 167 Q 60 170 91 167' fill='none' stroke='#E2E2E6' stroke-width='1'/>

  <!-- Tailgate glass: thin wrap-around sliver -->
  <path d='M 32 174
           L 88 174
           Q 93 174 92.5 179
           L 92 183
           Q 91.5 188 85 188
           L 35 188
           Q 28.5 188 28 183
           L 27.5 179
           Q 27 174 32 174 Z'
        fill='#0F0F13'/>

  <!-- Tailgate seam -->
  <path d='M 31 196 Q 60 200 89 196' fill='none' stroke='#E6E6EA' stroke-width='1.1'/>

  <!-- Full-width tail light blade, flush with the tailgate -->
  <path d='M 28 203 Q 60 208.5 92 203' fill='none' stroke='#E0473D' stroke-width='3.6' stroke-linecap='round'/>
</svg>"""
)


# Lux: same body language as the sedan, black paint, light outline so it
# stays readable on dark map styles.
LUX_SVG = (
    SVG_HEADER
    + "<defs>"
    + _shadow_defs("l")
    + """
    <linearGradient id='lbody' x1='0' y1='0' x2='1' y2='0'>
      <stop offset='0%' stop-color='#17171B'/>
      <stop offset='18%' stop-color='#26262C'/>
      <stop offset='50%' stop-color='#36363E'/>
      <stop offset='82%' stop-color='#26262C'/>
      <stop offset='100%' stop-color='#17171B'/>
    </linearGradient>
    <linearGradient id='lws' x1='0' y1='0' x2='0' y2='1'>
      <stop offset='0%' stop-color='#15151B'/>
      <stop offset='100%' stop-color='#0A0A0F'/>
    </linearGradient>
  </defs>

  <!-- Soft ground shadow -->
  <ellipse cx='60' cy='112' rx='49' ry='110' fill='url(#lshadow)'/>

  <!-- Side mirrors -->
  <rect x='14' y='92' width='13' height='6.5' rx='3.25' fill='#2A2A30' stroke='#6A6A74' stroke-width='0.8' transform='rotate(-20 20.5 95)'/>
  <rect x='93' y='92' width='13' height='6.5' rx='3.25' fill='#2A2A30' stroke='#6A6A74' stroke-width='0.8' transform='rotate(20 99.5 95)'/>

  <!-- Body -->
  <path d='M 60 8
           C 40 8 27 21 26 47
           C 25.4 60 25 74 25 92
           L 25 174
           C 25 197 35 211 60 211
           C 85 211 95 197 95 174
           L 95 92
           C 95 74 94.6 60 94 47
           C 93 21 80 8 60 8 Z'
        fill='url(#lbody)' stroke='#73737E' stroke-width='1.2'/>

  <!-- Hood seam -->
  <path d='M 36 33 Q 60 28 84 33' fill='none' stroke='#46464E' stroke-width='1.1'/>

  <!-- Windshield: black glass -->
  <path d='M 38 52
           C 46 50 74 50 82 52
           C 87 53 88.6 55.5 89 60
           L 91 89
           Q 91.6 98 83 98
           L 37 98
           Q 28.4 98 29 89
           L 31 60
           C 31.4 55.5 33 53 38 52 Z'
        fill='url(#lws)'/>

  <!-- Side window slivers -->
  <path d='M 28.5 103 Q 26.8 126 28.5 149 Q 30.5 152 32.5 149 Q 31.2 126 32.5 103 Q 30.5 100 28.5 103 Z' fill='#08080B'/>
  <path d='M 91.5 103 Q 93.2 126 91.5 149 Q 89.5 152 87.5 149 Q 88.8 126 87.5 103 Q 89.5 100 91.5 103 Z' fill='#08080B'/>

  <!-- Roof seams -->
  <path d='M 34 104 Q 60 101 86 104' fill='none' stroke='#46464E' stroke-width='1'/>
  <path d='M 34 149 Q 60 152 86 149' fill='none' stroke='#46464E' stroke-width='1'/>

  <!-- Rear window -->
  <path d='M 37 155
           L 83 155
           Q 88 155 88 161
           L 86.5 177
           Q 86 183 80 183
           L 40 183
           Q 34 183 33.5 177
           L 32 161
           Q 32 155 37 155 Z'
        fill='#0A0A0E'/>

  <!-- Trunk seam -->
  <path d='M 36 192 Q 60 196 84 192' fill='none' stroke='#46464E' stroke-width='1.1'/>

  <!-- Full-width tail light blade, flush with body -->
  <path d='M 33 202 Q 60 207.5 87 202' fill='none' stroke='#F0524A' stroke-width='3.4' stroke-linecap='round'/>
</svg>"""
)

VARIANTS = {
    "car_marker_sedan.png": SEDAN_SVG,
    "car_marker_suv.png": SUV_SVG,
    "car_marker_lux.png": LUX_SVG,
}


def main() -> None:
    for filename, svg in VARIANTS.items():
        out = OUT_DIR / filename
        cairosvg.svg2png(
            bytestring=_expand_hex8(svg).encode("utf-8"),
            write_to=str(out),
            output_width=120 * SCALE,
            output_height=220 * SCALE,
        )
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
