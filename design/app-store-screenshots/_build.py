"""Spinr rider App Store set — v3 "store convention" layout.

Layout follows the dominant pattern across top App Store / Play listings
(Uber, Lyft, DoorDash, Duolingo): centered headline up top, straight-on
centered device, solid brand-red hero frame then clean warm-paper frames,
floating feature chips overlapping the device edges.
"""

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
    .sp .chip {{ position: absolute; z-index: 5; display: flex; align-items: center; gap: 22px; padding: 26px 32px; border-radius: 32px; background: #FFFFFF; box-shadow: 0 24px 60px rgba(26,26,26,0.20), 0 2px 0 rgba(255,255,255,0.9) inset; }}
    .sp .chipico {{ flex-shrink: 0; width: 78px; height: 78px; border-radius: 39px; background: #FFF0F0; display: flex; align-items: center; justify-content: center; }}
  </style>
</helmet>

<div class="sp" style="width: 1290px; height: 2796px; position: relative; overflow: hidden; background: {bg};">

  {decor}

  <div style="position: relative; z-index: 2; display: flex; flex-direction: column; align-items: center; padding: 96px 90px 0; text-align: center;">
    <img src="{logo}" alt="Spinr" style="width: 204px; height: 83px; object-fit: contain;">
{badge}    <h1 style="margin: 44px 0 0; font-size: 112px; line-height: 1.04; font-weight: 800; letter-spacing: -0.04em; color: {hcolor}; text-wrap: balance;">{headline}</h1>
    <p style="margin: 30px 0 0; max-width: 960px; font-size: 37px; line-height: 1.48; font-weight: 500; color: {scolor}; text-wrap: pretty;">{sub}</p>
  </div>

  <div style="position: absolute; left: 179px; top: {ptop}px; width: 932px; height: 1977px; z-index: 3;">
    <div style="width: 858px; height: 1820px; transform: scale(1.0862); transform-origin: top left;">
      <div style="width: 858px; height: 1820px; border-radius: 132px; padding: 5px; background: linear-gradient(150deg, #55585F 0%, #2B2D33 28%, #101114 70%, #33353B 100%); box-shadow: 0 60px 120px rgba(20,10,8,0.40), 0 0 0 1px rgba(0,0,0,0.35);">
        <div style="width: 848px; height: 1810px; border-radius: 127px; background: #050505; padding: 9px;">
          <div style="width: 830px; height: 1792px; border-radius: 112px; overflow: hidden; background: {screenbg}; position: relative; display: flex; flex-direction: column;">
{screen}
            <div style="position: absolute; top: 22px; left: 287px; width: 256px; height: 74px; border-radius: 37px; background: #050505; z-index: 30;"></div>
          </div>
        </div>
      </div>
    </div>
  </div>

{chips}
</div>
</x-dc>
</body>
</html>
'''

def chip(side, top, icon, bold, small, width=430):
    pos = f'left: 44px' if side == 'l' else f'left: {1290 - 44 - width}px'
    return f'''  <div class="chip" style="{pos}; top: {top}px; width: {width}px;">
    <div class="chipico">{icon}</div>
    <div style="display: flex; flex-direction: column; gap: 4px; text-align: left;">
      <span style="font-size: 31px; font-weight: 800; letter-spacing: -0.01em; color: #1A1A1A;">{bold}</span>
      <span style="font-size: 24px; font-weight: 500; line-height: 1.3; color: #6B7280;">{small}</span>
    </div>
  </div>'''

# soft brand shapes — one large disc bleeding off an edge + one thin ring, alternating corners
def decor_paper(corner):
    if corner == 'r':
        return ('<div style="position: absolute; top: 620px; right: -320px; width: 900px; height: 900px; border-radius: 450px; background: #FFE9E7; z-index: 0;"></div>'
                '<div style="position: absolute; top: 2350px; left: -180px; width: 520px; height: 520px; border-radius: 260px; border: 3px solid #FFD1CD; z-index: 0;"></div>')
    return ('<div style="position: absolute; top: 620px; left: -320px; width: 900px; height: 900px; border-radius: 450px; background: #FFE9E7; z-index: 0;"></div>'
            '<div style="position: absolute; top: 2350px; right: -180px; width: 520px; height: 520px; border-radius: 260px; border: 3px solid #FFD1CD; z-index: 0;"></div>')

DECOR_RED = ('<div style="position: absolute; top: -260px; right: -300px; width: 980px; height: 980px; border-radius: 490px; background: rgba(255,255,255,0.07); z-index: 0;"></div>'
             '<div style="position: absolute; top: 2300px; left: -220px; width: 640px; height: 640px; border-radius: 320px; background: rgba(0,0,0,0.10); z-index: 0;"></div>'
             '<div style="position: absolute; top: 520px; left: 90px; width: 260px; height: 260px; border-radius: 130px; border: 3px solid rgba(255,255,255,0.22); z-index: 0;"></div>')

I = {
 'zero':  '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1v22"></path><path d="M17.5 6.5H9.75a3.25 3.25 0 0 0 0 6.5h4.5a3.25 3.25 0 0 1 0 6.5H6"></path></svg>',
 'pin':   '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 21s7-5.5 7-11a7 7 0 1 0-14 0c0 5.5 7 11 7 11z"></path><circle cx="12" cy="10" r="2.6"></circle></svg>',
 'eye':   '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z"></path><circle cx="12" cy="12" r="3"></circle></svg>',
 'cal':   '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><rect x="3.5" y="5" width="17" height="15.5" rx="3"></rect><path d="M3.5 10h17M8 3v4M16 3v4"></path></svg>',
 'share': '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="18" cy="5" r="3"></circle><circle cx="6" cy="12" r="3"></circle><circle cx="18" cy="19" r="3"></circle><path d="M8.6 13.5l6.8 4M15.4 6.5l-6.8 4"></path></svg>',
 'check': '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#34C759" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6L9 17l-5-5"></path></svg>',
 'doc':   '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M14 2.5H6.5a2 2 0 0 0-2 2v15a2 2 0 0 0 2 2h11a2 2 0 0 0 2-2V8z"></path><path d="M14 2.5V8h5.5M8.5 13h7M8.5 17h5"></path></svg>',
 'shield':'<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.5l8 3.2v6c0 5-3.4 8.8-8 10-4.6-1.2-8-5-8-10v-6z"></path><path d="M9 12l2.2 2.2L15.5 10"></path></svg>',
 'bell':  '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 9a6 6 0 1 0-12 0c0 6-2.5 7.5-2.5 7.5h17S18 15 18 9z"></path><path d="M10 20.5a2.2 2.2 0 0 0 4 0"></path></svg>',
}

LEAF_D = 'M500 30 l-61 116 c-7 13 -19 12 -32 5 l-98 -51 74 383 c3 15 -8 21 -19 9 l-95 -110 -26 66 c-3 8 -12 7 -19 6 l-105 -22 36 131 c3 11 5 20 -6 24 l-38 13 181 148 c9 9 13 24 9 38 l-16 54 176 -30 c9 -2 22 2 22 12 l-8 179 h30 l-8 -179 c0 -10 13 -14 22 -12 l176 30 -16 -54 c-4 -14 0 -29 9 -38 l181 -148 -38 -13 c-11 -4 -9 -13 -6 -24 l36 -131 -105 22 c-7 1 -16 2 -19 -6 l-26 -66 -95 110 c-11 12 -22 6 -19 -9 l74 -383 -98 51 c-13 7 -25 8 -32 -5 z'
I.update({
 'spark': '<svg width="38" height="38" viewBox="0 0 24 24" fill="#FF3B30"><path d="M12 2l2.1 5.6L20 9.5l-5.9 1.9L12 17l-2.1-5.6L4 9.5l5.9-1.9z"></path></svg>',
 'bolt':  '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L4.5 13.5H11L10 22l8.5-11.5H12z"></path></svg>',
 'chat':  '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.4" stroke-linejoin="round"><path d="M21 11.5a8 8 0 0 1-8.5 8 9 9 0 0 1-3.3-.6L4 20.5l1.6-4.6A8 8 0 0 1 4.5 11 8 8 0 0 1 13 3.5a8 8 0 0 1 8 8z"></path></svg>',
 'tag2':  '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20.5 13.5L13 21a2 2 0 0 1-2.8 0l-7-7A2 2 0 0 1 2.6 12.6V5a2 2 0 0 1 2-2h7.6a2 2 0 0 1 1.4.6l6.9 6.9a2 2 0 0 1 0 2.8z"></path><circle cx="8" cy="8" r="1.6"></circle></svg>',
})

PAPER = '#FAF6F4'
RED   = '#E8352B'

BADGE_PC = '''    <div style="display: flex; align-items: center; gap: 14px; margin-top: 34px; padding: 16px 34px; border-radius: 999px; background: #FFFFFF; box-shadow: 0 10px 30px rgba(0,0,0,0.18);">
      <svg width="34" height="34" viewBox="0 0 1000 1000"><path d="M500 30 l-61 116 c-7 13 -19 12 -32 5 l-98 -51 74 383 c3 15 -8 21 -19 9 l-95 -110 -26 66 c-3 8 -12 7 -19 6 l-105 -22 36 131 c3 11 5 20 -6 24 l-38 13 181 148 c9 9 13 24 9 38 l-16 54 176 -30 c9 -2 22 2 22 12 l-8 179 h30 l-8 -179 c0 -10 13 -14 22 -12 l176 30 -16 -54 c-4 -14 0 -29 9 -38 l181 -148 -38 -13 c-11 -4 -9 -13 -6 -24 l36 -131 -105 22 c-7 1 -16 2 -19 -6 l-26 -66 -95 110 c-11 12 -22 6 -19 -9 l74 -383 -98 51 c-13 7 -25 8 -32 -5 z" fill="#E8352B"/></svg>
      <span style="font-size: 26px; font-weight: 800; letter-spacing: 0.14em; color: #1A1A1A;">PROUDLY CANADIAN</span>
    </div>
'''

FRAMES = [
 dict(out='Main.dc.html', screen='home', bg=RED, logo='spinr-logo-white.png', badge=BADGE_PC,
   decor=DECOR_RED, hcolor='#FFFFFF', scolor='rgba(255,255,255,0.88)', ptop=760,
   headline='Saskatchewan&rsquo;s<br>own ride app',
   sub='Built here, run here. Spinr takes no cut &mdash; 100% of your fare goes to your driver.',
   chips=[('l', 1010, I['zero'], '0% commission', 'Drivers keep every dollar'),
          ('r', 2280, I['pin'], 'Saskatoon &amp; Regina', 'Saskatchewan-first, always')]),

 dict(out='Rider02.dc.html', screen='options', bg=PAPER, logo='spinr-logo.png',
   decor=decor_paper('r'), hcolor='#1A1A1A', scolor='#6B7280', ptop=760,
   headline='Know the price<br>before you <span style="color: #E8352B;">ride</span>',
   sub='Economy to XL &mdash; every option and fare upfront, surge shown before you book.',
   chips=[('r', 1060, I['eye'], 'No surprises', 'The price you see is the price')]),

 dict(out='Rider03.dc.html', screen='tracking', bg=PAPER, logo='spinr-logo.png',
   decor=decor_paper('l'), hcolor='#1A1A1A', scolor='#6B7280', ptop=760,
   headline='Track every<br>ride, <span style="color: #E8352B;">live</span>',
   sub='Driver, plate and ETA on one screen &mdash; and one tap to share the trip with someone you trust.',
   chips=[('l', 1060, I['share'], 'Share your trip', 'Live location until you arrive'),
          ('r', 2020, I['eye'], 'Check the plate', 'Match it before you hop in')]),

 dict(out='Rider04.dc.html', screen='ai', bg=PAPER, logo='spinr-logo.png',
   decor=decor_paper('r'), hcolor='#1A1A1A', scolor='#6B7280', ptop=760,
   headline='Your ride,<br>one <span style="color: #E8352B;">ask</span> away',
   sub='The built-in assistant answers fare, wallet and promo questions &mdash; and gets you a ride quote by chat.',
   chips=[('l', 1150, I['spark'], 'Book by chat', 'From quote to pickup'),
          ('r', 2240, I['bolt'], 'Instant answers', 'Fares, wallet and promos')]),

 dict(out='Rider05.dc.html', screen='support', bg=PAPER, logo='spinr-logo.png',
   decor=decor_paper('l'), hcolor='#1A1A1A', scolor='#6B7280', ptop=760,
   headline='Help, right<br>in the <span style="color: #E8352B;">app</span>',
   sub='Searchable FAQs, AI chat and a direct line to the Spinr team &mdash; no hold music.',
   chips=[('r', 2280, I['chat'], 'Real humans', 'The Spinr team replies'),
          ('l', 2600, I['tag2'], 'Lost &amp; found', 'Chat to get items back')]),
]

for f in FRAMES:
    screen = open(f"_screens/{f['screen']}.html").read()
    chips = '\n'.join(chip(*c) for c in f['chips'])
    html = TPL.format(bg=f['bg'], decor=f['decor'], logo=f['logo'], badge=f.get('badge',''), hcolor=f['hcolor'],
                      scolor=f['scolor'], ptop=f['ptop'], headline=f['headline'], sub=f['sub'],
                      screen=screen, screenbg='#FFFFFF', chips=chips)
    open(f['out'], 'w').write(html)
    d, dc = html.count('<div'), html.count('</div>')
    s, sc = html.count('<svg'), html.count('</svg>')
    print(f"{f['out']:16} screen={f['screen']:9} div {d}/{dc} svg {s}/{sc} {'OK' if d==dc and s==sc else 'MISMATCH'}")

# ---- Brand frame: Proudly Canadian, original logo untouched on white ----
BRAND = f'''<!doctype html>
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
    .sp .stat {{ display: flex; align-items: center; gap: 38px; padding: 64px 0; }}
  </style>
</helmet>

<div class="sp" style="width: 1290px; height: 2796px; position: relative; overflow: hidden; background: #E8352B; display: flex; flex-direction: column; align-items: center; padding: 130px 90px 0;">

  <div style="position: absolute; top: -260px; right: -300px; width: 980px; height: 980px; border-radius: 490px; background: rgba(255,255,255,0.07); z-index: 0;"></div>
  <div style="position: absolute; bottom: -220px; left: -260px; width: 760px; height: 760px; border-radius: 380px; background: rgba(0,0,0,0.10); z-index: 0;"></div>

  <img src="spinr-logo-white.png" alt="Spinr" style="position: relative; width: 236px; height: 96px; object-fit: contain;">

  <div style="position: relative; margin-top: 96px; width: 1110px; border-radius: 84px; background: #FFFFFF; box-shadow: 0 70px 150px rgba(0,0,0,0.30); padding: 110px 96px 96px; display: flex; flex-direction: column; align-items: center; text-align: center;">

    <svg width="360" height="360" viewBox="0 0 1000 1000"><path d="{{LEAF_D}}" fill="#E8352B"/></svg>

    <h1 style="margin: 80px 0 0; font-size: 156px; line-height: 1.02; font-weight: 800; letter-spacing: -0.04em; color: #1A1A1A;">Proudly<br><span style="color: #E8352B;">Canadian</span></h1>

    <p style="margin: 46px 0 0; max-width: 880px; font-size: 40px; line-height: 1.5; font-weight: 500; color: #6B7280; text-wrap: pretty;">Owned and operated in Saskatchewan &mdash; every fare stays in the community that paid it.</p>

    <div style="width: 100%; margin-top: 90px; display: flex; flex-direction: column;">
      <div class="stat" style="border-top: 2px solid #F0EDEA;">
        <span style="flex-shrink: 0; width: 300px; text-align: right; font-size: 96px; font-weight: 800; letter-spacing: -0.03em; color: #E8352B;">0%</span>
        <span style="text-align: left; font-size: 36px; font-weight: 600; color: #1A1A1A;">platform commission</span>
      </div>
      <div class="stat" style="border-top: 2px solid #F0EDEA;">
        <span style="flex-shrink: 0; width: 300px; text-align: right; font-size: 96px; font-weight: 800; letter-spacing: -0.03em; color: #E8352B;">100%</span>
        <span style="text-align: left; font-size: 36px; font-weight: 600; color: #1A1A1A;">of every fare to the driver</span>
      </div>
      <div class="stat" style="border-top: 2px solid #F0EDEA; border-bottom: 2px solid #F0EDEA;">
        <span style="flex-shrink: 0; width: 300px; text-align: right; font-size: 96px; font-weight: 800; letter-spacing: -0.03em; color: #E8352B;">2</span>
        <span style="text-align: left; font-size: 36px; font-weight: 600; color: #1A1A1A;">cities &mdash; Saskatoon &amp; Regina</span>
      </div>
    </div>
  </div>

  <div style="position: relative; display: flex; align-items: center; gap: 18px; margin-top: 110px;">
    <svg width="44" height="44" viewBox="0 0 1000 1000"><path d="{{LEAF_D}}" fill="#FFFFFF"/></svg>
    <span style="font-size: 32px; font-weight: 700; letter-spacing: 0.12em; color: #FFFFFF; text-transform: uppercase;">Built in Saskatchewan</span>
  </div>
</div>
</x-dc>
</body>
</html>
'''
BRAND = BRAND.replace('{LEAF_D}', LEAF_D)
open('Brand02.dc.html', 'w').write(BRAND)
d, dc = BRAND.count('<div'), BRAND.count('</div>')
print(f"Brand02.dc.html div {d}/{dc} svg {BRAND.count('<svg')}/{BRAND.count('</svg>')} {'OK' if d==dc else 'MISMATCH'}")
