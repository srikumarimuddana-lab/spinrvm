#!/usr/bin/env python3
"""Generate the top-down car marker PNGs used on the live maps.

Three variants, one per vehicle class (Uber/Lyft-style top-down cars,
nose pointing up, transparent background):

  - sedan : white compact sedan          -> car_marker_sedan.png
  - suv   : white boxy SUV w/ roof rails -> car_marker_suv.png
  - lux   : black luxury sedan           -> car_marker_lux.png

Canvas is 120x220 for every variant so `CarMarker`'s `size` prop scales
them identically; the SUV reads bigger via a wider, squarer body within
the same canvas. PNGs are rasterized at 3x (360x660) so they stay crisp
at marker sizes up to ~64 px.

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


def _white_paint_defs(prefix: str) -> str:
    """Silver-white body + blue glass gradients shared by sedan and SUV."""
    return f"""
  <defs>
    <linearGradient id='{prefix}bg' x1='0' y1='0' x2='1' y2='0'>
      <stop offset='0%' stop-color='#D4D4D8'/>
      <stop offset='15%' stop-color='#E8E8EC'/>
      <stop offset='50%' stop-color='#F8F8FA'/>
      <stop offset='85%' stop-color='#E8E8EC'/>
      <stop offset='100%' stop-color='#D4D4D8'/>
    </linearGradient>
    <linearGradient id='{prefix}gl' x1='0' y1='0' x2='0' y2='1'>
      <stop offset='0%' stop-color='#3B8DB5'/>
      <stop offset='100%' stop-color='#62B4D8'/>
    </linearGradient>
  </defs>"""


SEDAN_SVG = (
    SVG_HEADER
    + _white_paint_defs("s")
    + """
  <!-- Ground shadow -->
  <ellipse cx='60' cy='113' rx='38' ry='68' fill='#00000030'/>

  <!-- Body -->
  <rect x='22' y='8' width='76' height='204' rx='30' fill='url(#sbg)'/>
  <rect x='22' y='8' width='76' height='204' rx='30' fill='none' stroke='#C0C0C4' stroke-width='1.2'/>

  <!-- Front bumper -->
  <rect x='30' y='12' width='60' height='10' rx='5' fill='#CCCCCE'/>

  <!-- Headlights -->
  <rect x='28' y='14' width='15' height='7' rx='3.5' fill='#F5D560' stroke='#D4A830' stroke-width='0.6'/>
  <rect x='77' y='14' width='15' height='7' rx='3.5' fill='#F5D560' stroke='#D4A830' stroke-width='0.6'/>

  <!-- Grille -->
  <rect x='44' y='14' width='32' height='6' rx='3' fill='#AAAAB0'/>

  <!-- Hood line -->
  <path d='M 35 42 Q 60 37 85 42' fill='none' stroke='#B8B8BC' stroke-width='1'/>

  <!-- Windshield -->
  <rect x='32' y='42' width='56' height='46' rx='13' fill='url(#sgl)'/>
  <rect x='37' y='46' width='22' height='12' rx='6' fill='#FFFFFF50'/>

  <!-- A-pillars -->
  <line x1='32' y1='46' x2='37' y2='86' stroke='#D0D0D4' stroke-width='3' stroke-linecap='round'/>
  <line x1='88' y1='46' x2='83' y2='86' stroke='#D0D0D4' stroke-width='3' stroke-linecap='round'/>

  <!-- Roof -->
  <rect x='28' y='90' width='64' height='52' rx='8' fill='#EAEAEE'/>
  <rect x='40' y='98' width='40' height='32' rx='5' fill='#F2F2F6'/>

  <!-- C-pillars -->
  <line x1='34' y1='138' x2='32' y2='150' stroke='#D0D0D4' stroke-width='3' stroke-linecap='round'/>
  <line x1='86' y1='138' x2='88' y2='150' stroke='#D0D0D4' stroke-width='3' stroke-linecap='round'/>

  <!-- Rear window -->
  <rect x='35' y='146' width='50' height='34' rx='12' fill='url(#sgl)' opacity='0.85'/>

  <!-- Trunk line -->
  <path d='M 35 184 Q 60 189 85 184' fill='none' stroke='#B8B8BC' stroke-width='1'/>

  <!-- Rear bumper -->
  <rect x='30' y='198' width='60' height='10' rx='5' fill='#CCCCCE'/>

  <!-- Taillights -->
  <rect x='28' y='196' width='15' height='7' rx='3.5' fill='#E05050' stroke='#C03030' stroke-width='0.6'/>
  <rect x='77' y='196' width='15' height='7' rx='3.5' fill='#E05050' stroke='#C03030' stroke-width='0.6'/>

  <!-- License plate -->
  <rect x='46' y='200' width='28' height='5' rx='1.5' fill='#B8B8BC'/>

  <!-- Side mirrors -->
  <ellipse cx='14' cy='70' rx='8' ry='10' fill='#E0E0E4' stroke='#C0C0C4' stroke-width='0.8'/>
  <ellipse cx='14' cy='70' rx='4.5' ry='6' fill='#4A9EC0'/>
  <ellipse cx='106' cy='70' rx='8' ry='10' fill='#E0E0E4' stroke='#C0C0C4' stroke-width='0.8'/>
  <ellipse cx='106' cy='70' rx='4.5' ry='6' fill='#4A9EC0'/>

  <!-- Door lines + handles -->
  <line x1='25' y1='88' x2='25' y2='142' stroke='#C4C4C8' stroke-width='0.8'/>
  <line x1='95' y1='88' x2='95' y2='142' stroke='#C4C4C8' stroke-width='0.8'/>
  <rect x='23' y='112' width='5' height='3' rx='1.5' fill='#B0B0B6'/>
  <rect x='92' y='112' width='5' height='3' rx='1.5' fill='#B0B0B6'/>

  <!-- Wheel wells -->
  <ellipse cx='38' cy='38' rx='10' ry='6' fill='#A0A0A6'/>
  <ellipse cx='82' cy='38' rx='10' ry='6' fill='#A0A0A6'/>
  <ellipse cx='38' cy='184' rx='10' ry='6' fill='#A0A0A6'/>
  <ellipse cx='82' cy='184' rx='10' ry='6' fill='#A0A0A6'/>
</svg>"""
)


# SUV: wider, squarer body; long cabin glass; roof rails; short rear hatch.
SUV_SVG = (
    SVG_HEADER
    + _white_paint_defs("u")
    + """
  <!-- Ground shadow -->
  <ellipse cx='60' cy='112' rx='44' ry='74' fill='#00000034'/>

  <!-- Body: wider and squarer than the sedan -->
  <rect x='17' y='6' width='86' height='210' rx='20' fill='url(#ubg)'/>
  <rect x='17' y='6' width='86' height='210' rx='20' fill='none' stroke='#BCBCC0' stroke-width='1.4'/>

  <!-- Front bumper + skid plate -->
  <rect x='25' y='10' width='70' height='11' rx='5' fill='#CCCCCE'/>
  <rect x='44' y='8' width='32' height='4' rx='2' fill='#9A9AA0'/>

  <!-- Headlights (slimmer, wider — SUV light bar look) -->
  <rect x='23' y='13' width='19' height='7' rx='3' fill='#F5D560' stroke='#D4A830' stroke-width='0.6'/>
  <rect x='78' y='13' width='19' height='7' rx='3' fill='#F5D560' stroke='#D4A830' stroke-width='0.6'/>

  <!-- Grille -->
  <rect x='45' y='13' width='30' height='7' rx='3' fill='#A4A4AA'/>

  <!-- Hood: shorter than sedan, with crease lines -->
  <path d='M 30 38 Q 60 33 90 38' fill='none' stroke='#B8B8BC' stroke-width='1'/>
  <line x1='40' y1='22' x2='42' y2='36' stroke='#C8C8CC' stroke-width='0.9'/>
  <line x1='80' y1='22' x2='78' y2='36' stroke='#C8C8CC' stroke-width='0.9'/>

  <!-- Windshield: more upright (shorter) than sedan -->
  <rect x='26' y='38' width='68' height='38' rx='10' fill='url(#ugl)'/>
  <rect x='32' y='42' width='26' height='11' rx='5.5' fill='#FFFFFF50'/>

  <!-- A-pillars -->
  <line x1='26' y1='42' x2='30' y2='74' stroke='#CCCCD0' stroke-width='3.2' stroke-linecap='round'/>
  <line x1='94' y1='42' x2='90' y2='74' stroke='#CCCCD0' stroke-width='3.2' stroke-linecap='round'/>

  <!-- Roof: long cargo-back cabin -->
  <rect x='22' y='78' width='76' height='84' rx='7' fill='#EAEAEE'/>
  <rect x='36' y='86' width='48' height='66' rx='5' fill='#F2F2F6'/>

  <!-- Roof rails (the SUV tell at small sizes) -->
  <rect x='25' y='80' width='3.4' height='80' rx='1.7' fill='#9EA0A6'/>
  <rect x='91.6' y='80' width='3.4' height='80' rx='1.7' fill='#9EA0A6'/>
  <!-- Rail crossbars -->
  <rect x='27' y='96' width='66' height='2.6' rx='1.3' fill='#B0B2B8'/>
  <rect x='27' y='138' width='66' height='2.6' rx='1.3' fill='#B0B2B8'/>

  <!-- D-pillars -->
  <line x1='28' y1='160' x2='26' y2='172' stroke='#CCCCD0' stroke-width='3.2' stroke-linecap='round'/>
  <line x1='92' y1='160' x2='94' y2='172' stroke='#CCCCD0' stroke-width='3.2' stroke-linecap='round'/>

  <!-- Rear hatch window: wide and short -->
  <rect x='29' y='168' width='62' height='26' rx='9' fill='url(#ugl)' opacity='0.85'/>

  <!-- Rear bumper -->
  <rect x='25' y='202' width='70' height='10' rx='5' fill='#CCCCCE'/>

  <!-- Taillights (wide wrap-around) -->
  <rect x='23' y='199' width='19' height='8' rx='3.5' fill='#E05050' stroke='#C03030' stroke-width='0.6'/>
  <rect x='78' y='199' width='19' height='8' rx='3.5' fill='#E05050' stroke='#C03030' stroke-width='0.6'/>

  <!-- License plate -->
  <rect x='45' y='203' width='30' height='5.5' rx='1.5' fill='#B8B8BC'/>

  <!-- Side mirrors (bigger, mounted lower) -->
  <ellipse cx='10' cy='64' rx='8.5' ry='11' fill='#E0E0E4' stroke='#C0C0C4' stroke-width='0.8'/>
  <ellipse cx='10' cy='64' rx='5' ry='6.5' fill='#4A9EC0'/>
  <ellipse cx='110' cy='64' rx='8.5' ry='11' fill='#E0E0E4' stroke='#C0C0C4' stroke-width='0.8'/>
  <ellipse cx='110' cy='64' rx='5' ry='6.5' fill='#4A9EC0'/>

  <!-- Door lines + handles (two rows: 4-door + hatch) -->
  <line x1='20' y1='80' x2='20' y2='160' stroke='#C4C4C8' stroke-width='0.8'/>
  <line x1='100' y1='80' x2='100' y2='160' stroke='#C4C4C8' stroke-width='0.8'/>
  <rect x='18' y='100' width='5' height='3' rx='1.5' fill='#B0B0B6'/>
  <rect x='97' y='100' width='5' height='3' rx='1.5' fill='#B0B0B6'/>
  <rect x='18' y='132' width='5' height='3' rx='1.5' fill='#B0B0B6'/>
  <rect x='97' y='132' width='5' height='3' rx='1.5' fill='#B0B0B6'/>

  <!-- Wheel wells (larger) -->
  <ellipse cx='34' cy='36' rx='11' ry='7' fill='#A0A0A6'/>
  <ellipse cx='86' cy='36' rx='11' ry='7' fill='#A0A0A6'/>
  <ellipse cx='34' cy='186' rx='11' ry='7' fill='#A0A0A6'/>
  <ellipse cx='86' cy='186' rx='11' ry='7' fill='#A0A0A6'/>
</svg>"""
)


# Lux: black executive sedan (Uber Black style) — long hood, gloss roof,
# chrome trim. Light outline keeps it readable on dark map styles.
LUX_SVG = (
    SVG_HEADER
    + """
  <defs>
    <linearGradient id='lbg' x1='0' y1='0' x2='1' y2='0'>
      <stop offset='0%' stop-color='#141417'/>
      <stop offset='18%' stop-color='#26262B'/>
      <stop offset='50%' stop-color='#3A3A41'/>
      <stop offset='82%' stop-color='#26262B'/>
      <stop offset='100%' stop-color='#141417'/>
    </linearGradient>
    <linearGradient id='lgl' x1='0' y1='0' x2='0' y2='1'>
      <stop offset='0%' stop-color='#1E3A4A'/>
      <stop offset='100%' stop-color='#2E5A70'/>
    </linearGradient>
    <linearGradient id='lgloss' x1='0' y1='0' x2='1' y2='0'>
      <stop offset='0%' stop-color='#FFFFFF' stop-opacity='0'/>
      <stop offset='50%' stop-color='#FFFFFF' stop-opacity='0.18'/>
      <stop offset='100%' stop-color='#FFFFFF' stop-opacity='0'/>
    </linearGradient>
  </defs>

  <!-- Ground shadow -->
  <ellipse cx='60' cy='113' rx='38' ry='70' fill='#00000038'/>

  <!-- Body: long executive sedan -->
  <rect x='22' y='6' width='76' height='208' rx='32' fill='url(#lbg)'/>
  <rect x='22' y='6' width='76' height='208' rx='32' fill='none' stroke='#5A5A64' stroke-width='1.3'/>

  <!-- Front bumper + chrome trim -->
  <rect x='30' y='10' width='60' height='10' rx='5' fill='#2C2C32'/>
  <rect x='38' y='9' width='44' height='2.4' rx='1.2' fill='#C9CBD4'/>

  <!-- Headlights (slim LED) -->
  <rect x='28' y='13' width='17' height='5.5' rx='2.75' fill='#EAF3FF' stroke='#9FB8D8' stroke-width='0.6'/>
  <rect x='75' y='13' width='17' height='5.5' rx='2.75' fill='#EAF3FF' stroke='#9FB8D8' stroke-width='0.6'/>

  <!-- Grille (dark chrome frame) -->
  <rect x='47' y='12' width='26' height='7' rx='3.5' fill='#1A1A1E' stroke='#8A8C96' stroke-width='0.8'/>

  <!-- Long hood with center crease -->
  <path d='M 34 46 Q 60 41 86 46' fill='none' stroke='#4A4A52' stroke-width='1'/>
  <line x1='60' y1='22' x2='60' y2='42' stroke='#4A4A52' stroke-width='0.9'/>

  <!-- Windshield: raked (long) -->
  <rect x='32' y='46' width='56' height='44' rx='14' fill='url(#lgl)'/>
  <rect x='37' y='50' width='22' height='11' rx='5.5' fill='#FFFFFF38'/>

  <!-- A-pillars -->
  <line x1='32' y1='50' x2='37' y2='88' stroke='#46464E' stroke-width='3' stroke-linecap='round'/>
  <line x1='88' y1='50' x2='83' y2='88' stroke='#46464E' stroke-width='3' stroke-linecap='round'/>

  <!-- Roof with panoramic gloss -->
  <rect x='28' y='92' width='64' height='50' rx='9' fill='#222227'/>
  <rect x='38' y='98' width='44' height='38' rx='6' fill='#2C2C33'/>
  <rect x='28' y='92' width='64' height='50' rx='9' fill='url(#lgloss)'/>

  <!-- C-pillars -->
  <line x1='34' y1='138' x2='32' y2='150' stroke='#46464E' stroke-width='3' stroke-linecap='round'/>
  <line x1='86' y1='138' x2='88' y2='150' stroke='#46464E' stroke-width='3' stroke-linecap='round'/>

  <!-- Rear window -->
  <rect x='35' y='146' width='50' height='32' rx='12' fill='url(#lgl)' opacity='0.9'/>

  <!-- Trunk line + chrome strip -->
  <path d='M 35 184 Q 60 189 85 184' fill='none' stroke='#4A4A52' stroke-width='1'/>
  <rect x='40' y='192' width='40' height='2.2' rx='1.1' fill='#C9CBD4'/>

  <!-- Rear bumper -->
  <rect x='30' y='200' width='60' height='10' rx='5' fill='#2C2C32'/>

  <!-- Taillights (slim LED bar) -->
  <rect x='28' y='197' width='17' height='6' rx='3' fill='#D8403E' stroke='#A82826' stroke-width='0.6'/>
  <rect x='75' y='197' width='17' height='6' rx='3' fill='#D8403E' stroke='#A82826' stroke-width='0.6'/>

  <!-- License plate -->
  <rect x='46' y='202' width='28' height='5' rx='1.5' fill='#8A8C96'/>

  <!-- Side mirrors (body-color, chrome cap) -->
  <ellipse cx='14' cy='72' rx='8' ry='10' fill='#2A2A30' stroke='#5A5A64' stroke-width='0.8'/>
  <ellipse cx='14' cy='72' rx='4.5' ry='6' fill='#3E6A82'/>
  <ellipse cx='106' cy='72' rx='8' ry='10' fill='#2A2A30' stroke='#5A5A64' stroke-width='0.8'/>
  <ellipse cx='106' cy='72' rx='4.5' ry='6' fill='#3E6A82'/>

  <!-- Door lines + chrome handles -->
  <line x1='25' y1='90' x2='25' y2='142' stroke='#46464E' stroke-width='0.8'/>
  <line x1='95' y1='90' x2='95' y2='142' stroke='#46464E' stroke-width='0.8'/>
  <rect x='23' y='112' width='5' height='3' rx='1.5' fill='#B9BBC4'/>
  <rect x='92' y='112' width='5' height='3' rx='1.5' fill='#B9BBC4'/>

  <!-- Wheel wells -->
  <ellipse cx='38' cy='38' rx='10' ry='6' fill='#0E0E11'/>
  <ellipse cx='82' cy='38' rx='10' ry='6' fill='#0E0E11'/>
  <ellipse cx='38' cy='186' rx='10' ry='6' fill='#0E0E11'/>
  <ellipse cx='82' cy='186' rx='10' ry='6' fill='#0E0E11'/>
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
