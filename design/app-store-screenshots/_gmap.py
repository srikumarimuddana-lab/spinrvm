"""Google-Maps-style map SVG builder, faithful to the app's real map layer:
default Google provider style, RouteLine's #FF9500->#EE2B2B gradient polyline
(4pt, round caps), RoutePins spec (green dot pickup / red square dropoff,
30pt disc, 2pt white ring), CarMarker's real car_marker@3x.png sprites."""
import base64, math

LAND    = '#F0EEE9'
BLOCK   = '#E8E5DE'
FOOT    = '#DFDCD3'
PARK    = '#BDE3B0'
PARK2   = '#A9D89A'
WATER   = '#9FC6F2'
ROAD    = '#FFFFFF'
HWY     = '#F9CF7E'
HWYCASE = '#ECBD64'

G_START = (0xFF, 0x95, 0x00)   # ROUTE_GRADIENT_START
G_END   = (0xEE, 0x2B, 0x2B)   # ROUTE_GRADIENT_END
STROKE  = 9                    # 4pt at the 2.13x screen scale

CAR = 'data:image/png;base64,' + base64.b64encode(open('car_marker.png','rb').read()).decode()

def _lerp(t):
    return '#%02X%02X%02X' % tuple(round(G_START[i] + (G_END[i]-G_START[i])*t) for i in range(3))

def gradient_route(pts, chunks=14, width=STROKE):
    """RouteLine-style gradient: split the path into length-equal chunks,
    each drawn in the colour at its midpoint t (orange start -> red end)."""
    segs = []
    lens = [math.dist(pts[i], pts[i+1]) for i in range(len(pts)-1)]
    total = sum(lens)
    step = total / chunks
    out, acc, cur, prev_pt = [], 0.0, [pts[0]], pts[0]
    done = 0.0
    for i in range(len(pts)-1):
        a, b = pts[i], pts[i+1]
        seg = lens[i]; used = 0.0
        while used < seg - 1e-6:
            room = step - acc
            take = min(room, seg - used)
            t2 = (used + take) / seg
            nxt = (a[0] + (b[0]-a[0])*t2, a[1] + (b[1]-a[1])*t2)
            cur.append(nxt); acc += take; used += take; done += take
            if acc >= step - 1e-6:
                mid_t = (done - acc/2) / total
                d = 'M ' + ' L '.join(f'{p[0]:.1f} {p[1]:.1f}' for p in cur)
                out.append(f'<path d="{d}" fill="none" stroke="{_lerp(mid_t)}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>')
                cur = [nxt]; acc = 0.0
    if len(cur) > 1:
        d = 'M ' + ' L '.join(f'{p[0]:.1f} {p[1]:.1f}' for p in cur)
        out.append(f'<path d="{d}" fill="none" stroke="{_lerp(1.0)}" stroke-width="{width}" stroke-linecap="round" stroke-linejoin="round"/>')
    return '\n'.join(out)

def pin(x, y, kind):
    """RoutePins spec at 30pt -> 64px: disc + 4.3px white ring + white glyph."""
    r = 32
    if kind == 'pickup':
        return (f'<circle cx="{x}" cy="{y}" r="{r}" fill="#10B981" stroke="#FFFFFF" stroke-width="4.3"/>'
                f'<circle cx="{x}" cy="{y}" r="{r*0.34}" fill="#FFFFFF"/>')
    s = 2*r*0.30
    return (f'<circle cx="{x}" cy="{y}" r="{r}" fill="#EF4444" stroke="#FFFFFF" stroke-width="4.3"/>'
            f'<rect x="{x-s/2}" y="{y-s/2}" width="{s}" height="{s}" fill="#FFFFFF"/>')

def car(x, y, heading=0, size=94):
    h = size/2
    return (f'<g transform="translate({x},{y}) rotate({heading})">'
            f'<ellipse cx="0" cy="6" rx="{h*0.72}" ry="{h*0.5}" fill="rgba(0,0,0,0.16)"/>'
            f'<image href="{CAR}" x="{-h}" y="{-h}" width="{size}" height="{size}"/></g>')

def blue_dot(x, y):
    return (f'<circle cx="{x}" cy="{y}" r="52" fill="#4285F4" opacity="0.15"/>'
            f'<circle cx="{x}" cy="{y}" r="17" fill="#4285F4" stroke="#FFFFFF" stroke-width="6"/>')

def base_map(w, h):
    """Default-Google-style ground: warm land, building blocks, park, river, roads."""
    return f'''<rect width="{w}" height="{h}" fill="{LAND}"/>
<path d="M-40 {h*0.30} C {w*0.2} {h*0.26}, {w*0.32} {h*0.38}, {w*0.53} {h*0.36} C {w*0.75} {h*0.34}, {w*0.84} {h*0.22}, {w+40} {h*0.19} L {w+40} {h*0.30} C {w*0.84} {h*0.33}, {w*0.75} {h*0.45}, {w*0.53} {h*0.47} C {w*0.32} {h*0.49}, {w*0.2} {h*0.37}, -40 {h*0.41} Z" fill="{WATER}"/>
<g fill="{BLOCK}">
<rect x="40" y="40" width="200" height="150" rx="10"/><rect x="300" y="40" width="160" height="150" rx="10"/>
<rect x="530" y="40" width="260" height="96" rx="10"/><rect x="40" y="{h*0.48}" width="215" height="140" rx="10"/>
<rect x="330" y="{h*0.48}" width="200" height="140" rx="10"/><rect x="600" y="{h*0.48}" width="190" height="140" rx="10"/>
<rect x="40" y="{h*0.66}" width="330" height="130" rx="10"/><rect x="440" y="{h*0.66}" width="350" height="130" rx="10"/>
<rect x="40" y="{h*0.84}" width="200" height="{h*0.14}" rx="10"/><rect x="330" y="{h*0.84}" width="200" height="{h*0.14}" rx="10"/>
<rect x="600" y="{h*0.84}" width="190" height="{h*0.14}" rx="10"/>
</g>
<g fill="{FOOT}">
<rect x="60" y="58" width="76" height="52" rx="4"/><rect x="150" y="58" width="70" height="52" rx="4"/>
<rect x="60" y="122" width="90" height="50" rx="4"/><rect x="318" y="60" width="60" height="46" rx="4"/>
<rect x="390" y="60" width="52" height="46" rx="4"/><rect x="552" y="56" width="100" height="56" rx="4"/>
<rect x="60" y="{h*0.50}" width="80" height="48" rx="4"/><rect x="352" y="{h*0.50}" width="70" height="48" rx="4"/>
<rect x="622" y="{h*0.50}" width="72" height="48" rx="4"/><rect x="60" y="{h*0.68}" width="120" height="44" rx="4"/>
<rect x="464" y="{h*0.68}" width="130" height="44" rx="4"/>
</g>
<rect x="300" y="{h*0.185}" width="160" height="118" rx="12" fill="{PARK}"/>
<circle cx="340" cy="{h*0.22}" r="12" fill="{PARK2}"/><circle cx="392" cy="{h*0.26}" r="15" fill="{PARK2}"/>
<circle cx="430" cy="{h*0.21}" r="10" fill="{PARK2}"/>
<g stroke="{ROAD}" stroke-linecap="round" fill="none">
<path d="M0 {h*0.155} H{w} M0 {h*0.445} H{w} M0 {h*0.635} H{w} M0 {h*0.825} H{w}" stroke-width="17"/>
<path d="M275 0 V{h} M505 0 V{h}" stroke-width="17"/>
<path d="M0 {h*0.325} H{w} M0 {h*0.535} H{w} M0 {h*0.925} H{w}" stroke-width="9"/>
<path d="M130 0 V{h} M660 0 V{h}" stroke-width="9"/>
</g>
<path d="M-30 {h*0.60} C {w*0.3} {h*0.575}, {w*0.7} {h*0.575}, {w+30} {h*0.55}" fill="none" stroke="{HWYCASE}" stroke-width="26"/>
<path d="M-30 {h*0.60} C {w*0.3} {h*0.575}, {w*0.7} {h*0.575}, {w+30} {h*0.55}" fill="none" stroke="{HWY}" stroke-width="20"/>'''
