import json, os

GRAIN = ("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='220' height='220'%3E"
         "%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='0.85' numOctaves='4' stitchTiles='stitch'/%3E"
         "%3C/filter%3E%3Crect width='100%25' height='100%25' filter='url(%23n)' opacity='0.55'/%3E%3C/svg%3E")

TPL = '''<!doctype html>
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
    .sp .row {{ display: flex; align-items: center; justify-content: space-between; }}
    .sp .item {{ display: flex; align-items: center; gap: 26px; padding: 32px 34px; border-radius: 30px; background: #F5F5F5; }}
    .sp .ico {{ flex-shrink: 0; width: 90px; height: 90px; border-radius: 45px; background: #FFFFFF; display: flex; align-items: center; justify-content: center; }}
    .sp .veh {{ display: flex; align-items: center; gap: 28px; padding: 30px 32px; border-radius: 34px; background: #FFFFFF; border: 2px solid #E5E7EB; }}
    .sp .cap {{ display: flex; align-items: center; gap: 7px; padding: 5px 14px; border-radius: 16px; background: #F5F5F5; }}
  </style>
</helmet>

<div class="sp" style="width: 1290px; height: 2796px; position: relative; overflow: hidden; background: #0E1013;">

  <div style="position: absolute; inset: 0; background: radial-gradient(1000px 760px at {bx}% -6%, rgba(255,59,48,0.34), rgba(255,59,48,0) 62%), radial-gradient(760px 620px at {sx}% 30%, rgba(88,110,168,0.20), rgba(88,110,168,0) 60%), radial-gradient(900px 700px at 50% 108%, rgba(255,59,48,0.14), rgba(255,59,48,0) 58%);"></div>

  <svg width="1290" height="2796" viewBox="0 0 1290 2796" style="position: absolute; inset: 0; display: block;">
    <defs>
      <filter id="glow{n}" x="-30%" y="-60%" width="160%" height="220%"><feGaussianBlur stdDeviation="26"></feGaussianBlur></filter>
      <linearGradient id="rt{n}" x1="0" y1="0" x2="1" y2="0">
        <stop offset="0" stop-color="#FF3B30" stop-opacity="0.30"></stop>
        <stop offset="0.5" stop-color="#FF3B30" stop-opacity="1"></stop>
        <stop offset="1" stop-color="#FF3B30" stop-opacity="0.30"></stop>
      </linearGradient>
    </defs>
    <path d="M -80 {ye} C 380 {ye}, 520 {yx}, 1370 {yx}" fill="none" stroke="#FF3B30" stroke-width="30" opacity="0.62" filter="url(#glow{n})"></path>
    <path d="M -80 {ye} C 380 {ye}, 520 {yx}, 1370 {yx}" fill="none" stroke="url(#rt{n})" stroke-width="13" stroke-linecap="round"></path>
  </svg>

  <div style="position: absolute; inset: 0; background-image: url(&quot;{grain}&quot;); opacity: 0.055; mix-blend-mode: overlay;"></div>

  <div style="position: relative; padding: 92px 92px 0;">
    <div class="row">
      <img src="spinr-logo-light.png" alt="Spinr" style="width: 212px; height: 86px; object-fit: contain;">
      <span style="font-size: 26px; font-weight: 600; letter-spacing: 0.16em; color: #55585F;">{n} / 5</span>
    </div>

    <div style="display: inline-flex; align-items: center; gap: 13px; margin-top: 40px; padding: 13px 26px; border-radius: 999px; background: rgba(255,59,48,0.12); border: 1px solid rgba(255,59,48,0.30);">
      {eyeicon}
      <span style="font-size: 24px; font-weight: 700; letter-spacing: 0.15em; color: #FF8078; text-transform: uppercase;">{eyebrow}</span>
    </div>

    <h1 style="margin: 36px 0 0; font-size: 104px; line-height: 1.02; font-weight: 800; letter-spacing: -0.04em; color: #FFFFFF;">{headline}</h1>

    <p style="margin: 26px 0 0; max-width: 880px; font-size: 34px; line-height: 1.5; font-weight: 500; color: #94919A; text-wrap: pretty;">{sub}</p>
  </div>

  <div style="position: absolute; left: 216px; top: 730px; width: 858px; height: 1820px; perspective: 2400px;">
    <div style="width: 858px; height: 1820px; transform: rotateY(-8deg) rotateZ(-1.2deg); transform-style: preserve-3d;">
      <div style="width: 858px; height: 1820px; border-radius: 76px; padding: 14px; background: linear-gradient(150deg, #3A3E46 0%, #111317 38%, #0A0B0D 100%); box-shadow: 0 -6px 0 rgba(255,255,255,0.06) inset, 0 70px 150px rgba(0,0,0,0.62), 0 0 0 1px rgba(255,255,255,0.05);">
        <div style="width: 830px; height: 1792px; border-radius: 63px; overflow: hidden; background: {screenbg}; position: relative; display: flex; flex-direction: column;">
{screen}
        </div>
      </div>
    </div>
  </div>

{callout}
</div>
</x-dc>
</body>
</html>
'''

def bar(icon, text):
    return f'''  <div style="position: absolute; left: 92px; top: 2586px; width: 1106px; height: 140px; display: flex; align-items: center; gap: 28px; padding: 0 40px; border-radius: 40px; background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.13); backdrop-filter: blur(18px);">
    <div style="flex-shrink: 0; width: 76px; height: 76px; border-radius: 38px; background: rgba(255,59,48,0.16); display: flex; align-items: center; justify-content: center;">{icon}</div>
    <span style="font-size: 32px; font-weight: 600; line-height: 1.3; color: #E8E5E7;">{text}</span>
  </div>'''

ICON_PIN   = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#FF8078" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s7-5.5 7-11a7 7 0 1 0-14 0c0 5.5 7 11 7 11z"></path><circle cx="12" cy="10" r="2.6"></circle></svg>'
ICON_TAG   = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#FF8078" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.5 13.5L13 21a2 2 0 0 1-2.8 0l-7-7A2 2 0 0 1 2.6 12.6V5a2 2 0 0 1 2-2h7.6a2 2 0 0 1 1.4.6l6.9 6.9a2 2 0 0 1 0 2.8z"></path><circle cx="8" cy="8" r="1.6"></circle></svg>'
ICON_CLOCK = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#FF8078" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M12 7v5.4l3.4 2"></path></svg>'
ICON_DOLLR = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#FF8078" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1v22"></path><path d="M17.5 6.5H9.75a3.25 3.25 0 0 0 0 6.5h4.5a3.25 3.25 0 0 1 0 6.5H6"></path></svg>'
ICON_SHLD  = '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#FF8078" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.5l8 3.2v6c0 5-3.4 8.8-8 10-4.6-1.2-8-5-8-10v-6z"></path><path d="M9 12l2.2 2.2L15.5 10"></path></svg>'

FRAMES = [
 dict(out='Main.dc.html', n=1, screen='home', bx=52, sx=104, ye=1500, yx=1280,
   eyebrow='Saskatchewan first', eyeicon=ICON_PIN,
   headline='Your fare<br>stays <span style="color: #FF3B30;">home</span>',
   sub='Saskatchewan-built ride-sharing across Saskatoon and Regina. Spinr takes no cut of your fare.',
   callout=bar('<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#FF6B60" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1v22"></path><path d="M17.5 6.5H9.75a3.25 3.25 0 0 0 0 6.5h4.5a3.25 3.25 0 0 1 0 6.5H6"></path></svg>', '0% commission &mdash; 100% of every fare goes to your driver')),

 dict(out='Rider02.dc.html', n=2, screen='options', bx=24, sx=96, ye=1280, yx=1460,
   eyebrow='Upfront pricing', eyeicon=ICON_TAG,
   headline='See the price<br>before you <span style="color: #FF3B30;">tap</span>',
   sub='Every ride option and every fare, shown before you book &mdash; wheelchair-accessible rides included.',
   callout=bar('<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#FF6B60" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"></path><circle cx="12" cy="12" r="3"></circle></svg>', 'Surge is shown before you book, never applied after')),

 dict(out='Rider03.dc.html', n=3, screen='tracking', bx=76, sx=8, ye=1460, yx=1180,
   eyebrow='Live tracking', eyeicon=ICON_CLOCK,
   headline='Watch your<br>driver <span style="color: #FF3B30;">arrive</span>',
   sub='Live location, plate and ETA on one screen, with your driver a tap away.',
   callout=bar('<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#FF6B60" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle><path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4"></path></svg>', 'Share your live trip with anyone, in one tap')),

 dict(out='Rider04.dc.html', n=4, screen='receipt', bx=48, sx=100, ye=1180, yx=1400,
   eyebrow='0% commission', eyeicon=ICON_DOLLR,
   headline='Spinr&rsquo;s cut:<br><span style="color: #FF3B30;">$0.00</span>',
   sub='Base fare, distance, time and tax &mdash; itemised. Nothing skimmed off the top.',
   callout=bar('<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#FF6B60" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2.5H6.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8z"></path><path d="M14 2.5V8h5.5M8.5 13h7M8.5 17h5"></path></svg>', 'GST and PST shown as separate line items on every receipt')),

 dict(out='Rider05.dc.html', n=5, screen='safety', bx=68, sx=4, ye=1400, yx=1220,
   eyebrow='Safety built in', eyeicon=ICON_SHLD,
   headline='Help is one<br><span style="color: #FF3B30;">tap</span> away',
   sub='One-tap SOS reaches your emergency contacts and our safety team. It never auto-dials 911.',
   callout=bar('<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#FF6B60" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.5l8 3.2v6c0 5-3.4 8.8-8 10-4.6-1.2-8-5-8-10v-6z"></path><path d="M9 12l2.2 2.2L15.5 10"></path></svg>', 'Service animals always welcome &mdash; and WAV rides whenever a WAV driver is online')),
]

meta = json.load(open('_screens/meta.json'))
meta['tracking'] = {'bg': '#FFFFFF'}

for f in FRAMES:
    screen = open(f"_screens/{f['screen']}.html").read()
    html = TPL.format(n=f['n'], bx=f['bx'], sx=f['sx'], ye=f['ye'], yx=f['yx'], grain=GRAIN,
                      eyebrow=f['eyebrow'], eyeicon=f['eyeicon'], headline=f['headline'],
                      sub=f['sub'], screen=screen, screenbg=meta[f['screen']]['bg'],
                      callout=f['callout'])
    open(f['out'], 'w').write(html)
    d, dc = html.count('<div'), html.count('</div>')
    s, sc = html.count('<svg'), html.count('</svg>')
    print(f"{f['out']:16} n={f['n']} screen={f['screen']:9} div {d}/{dc} svg {s}/{sc} {'OK' if d==dc and s==sc else 'MISMATCH'}")
