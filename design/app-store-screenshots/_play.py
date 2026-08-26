"""Google Play phone set — the same six frames reflowed to 1080x1920 (9:16,
inside Play's 2:1 aspect limit). Reuses _build's device, palette, leaf and
frame copy; drops the floating chips (too small to read at this size)."""
import _build as B

W, H = 1080, 1920

SHELL = '''<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <script src="./support.js"></script>
</head>
<body>
<x-dc>
<helmet>
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@500;600;700;800&display=swap">
  <style>
    body {{ margin: 0; font-family: 'Plus Jakarta Sans', 'Segoe UI', system-ui, -apple-system, sans-serif; }}
    a {{ color: #FF453A; }} a:hover {{ color: #FF6F66; }}
    .sp, .sp * {{ box-sizing: border-box; }}
  </style>
</helmet>

<div class="sp" style="width: 1080px; height: 1920px; position: relative; overflow: hidden; background: {bg};">
{decor}
{content}
</div>
</x-dc>
</body>
</html>
'''

DECOR_RED = ('<div style="position: absolute; top: -200px; right: -240px; width: 700px; height: 700px; border-radius: 350px; background: rgba(255,255,255,0.06); z-index: 0;"></div>'
             '<div style="position: absolute; bottom: -240px; left: -220px; width: 620px; height: 620px; border-radius: 310px; background: rgba(0,0,0,0.10); z-index: 0;"></div>')

def decor_paper(corner):
    if corner == 'r':
        return ('<div style="position: absolute; top: 430px; right: -260px; width: 640px; height: 640px; border-radius: 320px; background: #FFE9E7; z-index: 0;"></div>'
                '<div style="position: absolute; bottom: -160px; left: -140px; width: 400px; height: 400px; border-radius: 200px; border: 3px solid #FFD1CD; z-index: 0;"></div>')
    return ('<div style="position: absolute; top: 430px; left: -260px; width: 640px; height: 640px; border-radius: 320px; background: #FFE9E7; z-index: 0;"></div>'
            '<div style="position: absolute; bottom: -160px; right: -140px; width: 400px; height: 400px; border-radius: 200px; border: 3px solid #FFD1CD; z-index: 0;"></div>')

home = open('_screens/home.html').read()

# ---- frames 1-2: the connected pair at Play size ----
f1 = B.iphone(home, 878, 400, scale=0.95, rot=-8) + '''
  <div style="position: absolute; inset: 0; z-index: 4; padding: 66px 64px 92px; display: flex; flex-direction: column; pointer-events: none;">
    <img src="spinr-logo-white.png" alt="Spinr" style="width: 168px; height: 68px; object-fit: contain; align-self: flex-start;">
    <h1 style="margin: 60px 0 0; font-size: 126px; line-height: 1.0; font-weight: 800; letter-spacing: -0.045em; color: #FFFFFF;">Proudly<br><span style="color: #1A1A1A;">Canadian</span><br>rideshare.</h1>
    <div style="margin-top: 38px; display: flex; align-items: center; gap: 16px; max-width: 700px;">
      <svg width="36" height="36" viewBox="0 0 1000 1000" style="flex-shrink: 0;"><path d="''' + B.LEAF_D + '''" fill="#FFFFFF"/></svg>
      <span style="font-size: 33px; font-weight: 700; letter-spacing: -0.01em; color: #FFFFFF;">Your fare stays home.</span>
    </div>
    <div style="margin-top: auto; display: flex; flex-direction: column; gap: 10px; max-width: 560px;">
      <span style="font-size: 42px; font-weight: 800; letter-spacing: -0.02em; color: #FFFFFF;">0% commission.</span>
      <span style="font-size: 27px; line-height: 1.42; font-weight: 500; color: rgba(255,255,255,0.88);">100% of every fare goes to your driver.</span>
    </div>
  </div>'''

f2 = B.iphone(home, -224, 400, scale=0.95, rot=-8) + '''
  <div style="position: absolute; right: 64px; top: 66px; z-index: 4;">
    <img src="spinr-logo-white.png" alt="Spinr" style="width: 196px; height: 80px; object-fit: contain;">
  </div>'''

# ---- frames 3-6: header + straight phone, copy reused from _build.FRAMES ----
def tpl_frame(fr, corner):
    screen = open(f"_screens/{fr['screen']}.html").read()
    logo = fr['logo']
    hcolor, scolor = fr['hcolor'], fr['scolor']
    phone = B.iphone(screen, 197, 442, scale=0.80, rot=0)
    return f'''{phone}
  <div style="position: relative; z-index: 2; display: flex; flex-direction: column; align-items: center; padding: 60px 64px 0; text-align: center;">
    <img src="{logo}" alt="Spinr" style="width: 150px; height: 61px; object-fit: contain;">
    <h1 style="margin: 28px 0 0; font-size: 74px; line-height: 1.05; font-weight: 800; letter-spacing: -0.04em; color: {hcolor}; text-wrap: balance;">{fr['headline']}</h1>
    <p style="margin: 18px 0 0; max-width: 850px; font-size: 26px; line-height: 1.45; font-weight: 500; color: {scolor}; text-wrap: pretty;">{fr['sub']}</p>
  </div>'''

frames = [
    ('Play01.dc.html', B.PANEL, DECOR_RED, f1),
    ('Play02.dc.html', B.PANEL, DECOR_RED, f2),
]
corners = ['r', 'l', 'r', 'l']
for i, fr in enumerate(B.FRAMES):
    frames.append((f'Play{i+3:02d}.dc.html', B.PAPER, decor_paper(corners[i]), tpl_frame(fr, corners[i])))

for out, bg, decor, content in frames:
    html = SHELL.format(bg=bg, decor=decor, content=content)
    open(out, 'w').write(html)
    d, dc = html.count('<div'), html.count('</div>')
    print(f"{out:16} div {d}/{dc} {'OK' if d == dc else 'MISMATCH'}")
