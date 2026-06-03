#!/usr/bin/env python3
"""
Spinr vehicle-type illustrations.

Clean, flat, modern side-profile cars in Spinr's white + red brand palette.
Uber-inspired in *spirit* (simple, premium, instantly readable) but drawn from
scratch so nothing is copied. One SVG per vehicle type, rendered to a
transparent PNG that can be uploaded in Admin → Vehicle Types.

Run:  python3 generate_vehicles.py
Out:  ./<slug>.svg  and  ./<slug>.png  for each type.
"""
import os
import cairosvg

# --- Brand palette -----------------------------------------------------------
RED = "#EE2B2B"          # Spinr brand red (matches rider-app)
RED_DARK = "#C81E26"     # accents / brake lights
RED_DEEP = "#A3161D"     # shadowed red
BODY_HI = "#FFFFFF"      # top of body
BODY_LO = "#E9ECF1"      # bottom of body (subtle vertical gradient)
BODY_EDGE = "#D5D9E0"    # panel edge line
GLASS_HI = "#5C6B7A"
GLASS_LO = "#2B3742"
CHROME = "#C7CDD6"
TIRE = "#23262C"
RIM = "#C9CED6"
RIM_HUB = "#9AA0AB"

W, H = 1000, 560

# --- Shared SVG building blocks ---------------------------------------------

def defs() -> str:
    return f"""
  <defs>
    <linearGradient id="body" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%"  stop-color="{BODY_HI}"/>
      <stop offset="62%" stop-color="#FAFBFC"/>
      <stop offset="100%" stop-color="{BODY_LO}"/>
    </linearGradient>
    <linearGradient id="glass" x1="0" y1="0" x2="0.15" y2="1">
      <stop offset="0%"  stop-color="{GLASS_HI}"/>
      <stop offset="100%" stop-color="{GLASS_LO}"/>
    </linearGradient>
    <linearGradient id="redband" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{RED}"/>
      <stop offset="100%" stop-color="{RED_DARK}"/>
    </linearGradient>
    <radialGradient id="rim" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0%"  stop-color="#FFFFFF"/>
      <stop offset="55%" stop-color="{RIM}"/>
      <stop offset="100%" stop-color="{RIM_HUB}"/>
    </radialGradient>
    <radialGradient id="ground" cx="0.5" cy="0.5" r="0.5">
      <stop offset="0%"  stop-color="rgba(20,24,31,0.22)"/>
      <stop offset="70%" stop-color="rgba(20,24,31,0.10)"/>
      <stop offset="100%" stop-color="rgba(20,24,31,0)"/>
    </radialGradient>
    <linearGradient id="sheen" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="rgba(255,255,255,0.9)"/>
      <stop offset="100%" stop-color="rgba(255,255,255,0)"/>
    </linearGradient>
  </defs>"""


def beltline(x1: float, x2: float, y: float, w: float = 14) -> str:
    """Bold solid-red brand stripe + a darker hairline under it for depth.
    Solid colours (not the gradient) because cairosvg drops gradients on a
    zero-height horizontal stroke."""
    return (f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{RED}" '
            f'stroke-width="{w}" stroke-linecap="round"/>'
            f'<line x1="{x1+8}" y1="{y+w*0.62:.1f}" x2="{x2-8}" y2="{y+w*0.62:.1f}" '
            f'stroke="{RED_DEEP}" stroke-width="3" stroke-linecap="round" opacity="0.7"/>')


def mirror(x: float, y: float) -> str:
    """Wing mirror with a red brand cap."""
    return (f'<g><path d="M {x} {y} q -22 -2 -30 12 q -2 8 8 9 l 22 -3 Z" '
            f'fill="{RED}" stroke="{RED_DARK}" stroke-width="1.5"/>'
            f'<path d="M {x-26} {y+14} l 8 7" stroke="{RED_DEEP}" stroke-width="3" '
            f'stroke-linecap="round"/></g>')


def wheel(cx: float, cy: float, r: float) -> str:
    """A clean alloy wheel with five subtle spokes."""
    spokes = ""
    import math
    for i in range(5):
        a = math.radians(i * 72 - 90)
        x2 = cx + (r * 0.46) * math.cos(a)
        y2 = cy + (r * 0.46) * math.sin(a)
        spokes += (f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                   f'stroke="{RIM_HUB}" stroke-width="{r*0.13:.1f}" stroke-linecap="round"/>')
    return f"""
  <g>
    <circle cx="{cx}" cy="{cy}" r="{r}" fill="{TIRE}"/>
    <circle cx="{cx}" cy="{cy}" r="{r}" fill="none" stroke="#0E1014" stroke-width="2"/>
    <circle cx="{cx}" cy="{cy}" r="{r*0.62:.1f}" fill="url(#rim)"/>
    {spokes}
    <circle cx="{cx}" cy="{cy}" r="{r*0.16:.1f}" fill="{RIM_HUB}"/>
    <circle cx="{cx}" cy="{cy}" r="{r*0.16:.1f}" fill="none" stroke="#7C828D" stroke-width="2"/>
  </g>"""


def frame(slug: str, inner: str) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
            f'viewBox="0 0 {W} {H}">{defs()}\n'
            f'  <ellipse cx="500" cy="476" rx="402" ry="40" fill="url(#ground)"/>\n'
            f'{inner}\n</svg>')


# --- Per-type illustrations --------------------------------------------------
# Each car faces left. Wheels drawn after the body so arches read cleanly.

def economy() -> str:
    """Compact 4-door sedan — Spinr Go."""
    fw, rw, wy, wr = 300, 712, 412, 60
    body = f"""
  <g>
    <!-- main body -->
    <path d="M 96 392
             C 104 352, 150 338, 214 330
             L 300 256 C 318 238, 348 230, 388 228
             L 612 228 C 660 228, 690 240, 712 268
             L 770 332 C 836 338, 884 352, 902 378
             C 912 392, 910 414, 902 428
             L 96 428 C 90 414, 90 404, 96 392 Z"
          fill="url(#body)" stroke="{BODY_EDGE}" stroke-width="2"/>
    <!-- greenhouse / glass -->
    <path d="M 318 256 C 332 244, 356 240, 392 240
             L 604 240 C 642 240, 666 250, 684 272
             L 720 318 L 300 318 Z"
          fill="url(#glass)"/>
    <!-- B-pillar + door split -->
    <line x1="506" y1="240" x2="506" y2="318" stroke="rgba(255,255,255,0.35)" stroke-width="6"/>
    <line x1="500" y1="330" x2="500" y2="420" stroke="{BODY_EDGE}" stroke-width="2"/>
    <!-- red brand beltline + lower rocker accent -->
    {beltline(118, 884, 372, 14)}
    <line x1="240" y1="416" x2="772" y2="416" stroke="{RED}" stroke-width="7" stroke-linecap="round" opacity="0.9"/>
    <!-- door handles -->
    <rect x="430" y="350" width="34" height="9" rx="4.5" fill="{CHROME}"/>
    <rect x="556" y="350" width="34" height="9" rx="4.5" fill="{CHROME}"/>
    <!-- roof sheen -->
    <path d="M 330 246 C 360 240, 600 240, 676 268 L 660 252 C 600 244, 360 244, 340 250 Z"
          fill="url(#sheen)" opacity="0.7"/>
    {mirror(296, 318)}
    <!-- lights -->
    <path d="M 96 392 C 92 396, 92 408, 100 410 L 132 408 L 132 386 L 104 386 Z" fill="#F4F6F8"/>
    <rect x="100" y="392" width="30" height="12" rx="4" fill="#EAF6FF"/>
    <path d="M 902 384 L 872 384 L 872 406 L 900 406 C 906 402, 906 388, 902 384 Z" fill="{RED}"/>
  </g>"""
    return frame("economy", body + wheel(fw, wy, wr) + wheel(rw, wy, wr) + arches(fw, rw, wy, wr))


def premium() -> str:
    """Sleek, longer, lower executive sedan — Spinr Premium."""
    fw, rw, wy, wr = 296, 724, 414, 62
    body = f"""
  <g>
    <path d="M 88 396
             C 96 350, 150 336, 220 330
             L 322 250 C 344 232, 378 224, 420 223
             L 600 223 C 652 223, 686 236, 706 266
             L 772 330 C 846 336, 896 352, 912 380
             C 920 394, 916 416, 906 430
             L 88 430 C 82 416, 82 408, 88 396 Z"
          fill="url(#body)" stroke="{BODY_EDGE}" stroke-width="2"/>
    <path d="M 336 250 C 352 238, 380 234, 422 234
             L 592 234 C 636 234, 660 244, 678 268
             L 716 314 L 312 314 Z"
          fill="url(#glass)"/>
    <line x1="500" y1="234" x2="500" y2="314" stroke="rgba(255,255,255,0.35)" stroke-width="6"/>
    <line x1="494" y1="326" x2="494" y2="422" stroke="{BODY_EDGE}" stroke-width="2"/>
    <!-- bold red beltline + a fine red roof-line for the premium two-tone feel -->
    {beltline(110, 898, 366, 15)}
    <line x1="356" y1="230" x2="676" y2="230" stroke="{RED}" stroke-width="5" stroke-linecap="round" opacity="0.85"/>
    <line x1="150" y1="408" x2="858" y2="408" stroke="{RED_DARK}" stroke-width="5" stroke-linecap="round" opacity="0.7"/>
    <rect x="408" y="346" width="40" height="9" rx="4.5" fill="{CHROME}"/>
    <rect x="548" y="346" width="40" height="9" rx="4.5" fill="{CHROME}"/>
    <path d="M 344 240 C 380 234, 590 234, 670 264 L 652 248 C 590 238, 380 238, 354 244 Z"
          fill="url(#sheen)" opacity="0.75"/>
    {mirror(312, 312)}
    <rect x="92" y="392" width="34" height="13" rx="4" fill="#EAF6FF"/>
    <path d="M 912 384 L 876 384 L 876 406 L 908 408 C 916 404, 916 388, 912 384 Z" fill="{RED}"/>
  </g>"""
    return frame("premium", body + wheel(fw, wy, wr) + wheel(rw, wy, wr) + arches(fw, rw, wy, wr))


def suv() -> str:
    """Raised 5-seat SUV — taller than a sedan, sloped tailgate, chunky stance."""
    fw, rw, wy, wr = 292, 700, 420, 78
    body = f"""
  <g>
    <!-- body: tall front, flat roof over cabin, sloped rear hatch -->
    <path d="M 86 348
             C 90 300, 132 282, 188 278
             L 252 184 C 266 166, 294 158, 330 158
             L 632 158 C 672 158, 700 168, 716 196
             L 760 286
             C 836 292, 892 308, 910 340
             C 920 358, 918 414, 906 436
             L 86 436 C 80 418, 80 372, 86 348 Z"
          fill="url(#body)" stroke="{BODY_EDGE}" stroke-width="2"/>
    <!-- greenhouse: windshield + two side windows + small sloped quarter -->
    <path d="M 266 184 C 278 172, 302 166, 332 166
             L 628 166 C 662 166, 686 176, 700 200
             L 736 282 L 206 282
             C 212 246, 234 212, 266 184 Z"
          fill="url(#glass)"/>
    <line x1="326" y1="166" x2="296" y2="282" stroke="rgba(255,255,255,0.45)" stroke-width="7"/>
    <line x1="500" y1="166" x2="500" y2="282" stroke="rgba(255,255,255,0.32)" stroke-width="6"/>
    <line x1="648" y1="172" x2="690" y2="282" stroke="rgba(255,255,255,0.32)" stroke-width="6"/>
    <line x1="500" y1="294" x2="500" y2="430" stroke="{BODY_EDGE}" stroke-width="2"/>
    <!-- bold red beltline -->
    {beltline(104, 894, 320, 16)}
    <!-- roof rails -->
    <rect x="312" y="151" width="372" height="9" rx="4.5" fill="{CHROME}"/>
    <rect x="356" y="356" width="44" height="11" rx="5.5" fill="{CHROME}"/>
    <rect x="556" y="356" width="44" height="11" rx="5.5" fill="{CHROME}"/>
    <path d="M 276 178 C 320 168, 600 168, 690 196 L 670 180 C 600 172, 320 172, 292 180 Z"
          fill="url(#sheen)" opacity="0.65"/>
    {mirror(262, 296)}
    <rect x="90" y="330" width="36" height="20" rx="5" fill="#EAF6FF"/>
    <path d="M 910 322 L 872 322 L 872 364 L 902 364 C 912 356, 912 330, 910 322 Z" fill="{RED}"/>
    <!-- rugged lower cladding (dark) with a red rocker line -->
    <path d="M 92 408 L 902 408 L 898 436 L 86 436 Z" fill="rgba(35,38,44,0.15)"/>
    <line x1="210" y1="406" x2="790" y2="406" stroke="{RED}" stroke-width="7" stroke-linecap="round"/>
  </g>"""
    return frame("suv", body + wheel(fw, wy, wr) + wheel(rw, wy, wr) + arches(fw, rw, wy, wr))


def van() -> str:
    """Tall, long 6-seat passenger van — Spinr Van / XL."""
    fw, rw, wy, wr = 268, 744, 420, 66
    body = f"""
  <g>
    <path d="M 78 318
             C 82 280, 120 250, 168 226
             L 250 188 C 268 180, 296 176, 330 176
             L 858 176 C 894 176, 916 196, 920 232
             L 922 392 C 922 414, 916 428, 906 436
             L 78 436 C 72 418, 72 348, 78 318 Z"
          fill="url(#body)" stroke="{BODY_EDGE}" stroke-width="2"/>
    <!-- windshield -->
    <path d="M 170 226 L 250 192 C 264 186, 286 184, 312 184 L 312 286 L 120 286
             C 124 262, 144 242, 170 226 Z" fill="url(#glass)"/>
    <!-- side windows row -->
    <path d="M 332 184 L 858 184 C 884 184, 900 196, 902 220 L 902 284 L 332 284 Z"
          fill="url(#glass)"/>
    <line x1="332" y1="184" x2="332" y2="284" stroke="rgba(255,255,255,0.5)" stroke-width="7"/>
    <line x1="486" y1="184" x2="486" y2="284" stroke="rgba(255,255,255,0.35)" stroke-width="6"/>
    <line x1="640" y1="184" x2="640" y2="284" stroke="rgba(255,255,255,0.35)" stroke-width="6"/>
    <line x1="780" y1="184" x2="780" y2="284" stroke="rgba(255,255,255,0.35)" stroke-width="6"/>
    <!-- sliding door track + handle -->
    <line x1="500" y1="296" x2="500" y2="430" stroke="{BODY_EDGE}" stroke-width="2"/>
    <rect x="556" y="330" width="42" height="10" rx="5" fill="{CHROME}"/>
    <!-- bold red beltline + lower rocker accent -->
    {beltline(96, 904, 322, 17)}
    <line x1="150" y1="404" x2="850" y2="404" stroke="{RED}" stroke-width="7" stroke-linecap="round" opacity="0.9"/>
    <path d="M 120 300 C 200 250, 320 196, 340 192 L 320 200 C 250 220, 150 282, 120 312 Z"
          fill="url(#sheen)" opacity="0.55"/>
    {mirror(338, 250)}
    <!-- front light -->
    <rect x="82" y="300" width="36" height="20" rx="5" fill="#EAF6FF"/>
    <!-- rear light -->
    <path d="M 916 300 L 884 300 L 884 364 L 912 366 C 920 360, 920 306, 916 300 Z" fill="{RED}"/>
  </g>"""
    return frame("van", body + wheel(fw, wy, wr) + wheel(rw, wy, wr) + arches(fw, rw, wy, wr))


def arches(fw: float, rw: float, wy: float, wr: float) -> str:
    """Thin dark wheel-arch outlines drawn over the tires for a clean cut."""
    a = wr + 6
    return f"""
  <g fill="none" stroke="rgba(20,24,31,0.18)" stroke-width="3">
    <path d="M {fw-a} {wy} A {a} {a} 0 0 1 {fw+a} {wy}"/>
    <path d="M {rw-a} {wy} A {a} {a} 0 0 1 {rw+a} {wy}"/>
  </g>"""


# --- Render ------------------------------------------------------------------
VEHICLES = {
    "spinr-economy": ("Spinr Go — economy 4-seat sedan", economy),
    "spinr-premium": ("Spinr Premium — executive sedan", premium),
    "spinr-suv":     ("Spinr SUV — 5-seat SUV", suv),
    "spinr-van":     ("Spinr Van — 6-seat passenger van", van),
}

here = os.path.dirname(os.path.abspath(__file__))
for slug, (label, fn) in VEHICLES.items():
    svg = fn()
    svg_path = os.path.join(here, f"{slug}.svg")
    png_path = os.path.join(here, f"{slug}.png")
    with open(svg_path, "w") as f:
        f.write(svg)
    cairosvg.svg2png(bytestring=svg.encode(), write_to=png_path,
                     output_width=1000, output_height=560)
    size_kb = os.path.getsize(png_path) / 1024
    print(f"  {slug:16s} {size_kb:6.1f} KB  ({label})")

print("Done.")
