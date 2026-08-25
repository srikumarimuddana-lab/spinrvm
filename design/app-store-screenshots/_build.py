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
    <h1 style="margin: 44px 0 0; font-size: 112px; line-height: 1.04; font-weight: 800; letter-spacing: -0.04em; color: {hcolor}; text-wrap: balance;">{headline}</h1>
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

I.update({
 'spark': '<svg width="38" height="38" viewBox="0 0 24 24" fill="#FF3B30"><path d="M12 2l2.1 5.6L20 9.5l-5.9 1.9L12 17l-2.1-5.6L4 9.5l5.9-1.9z"></path></svg>',
 'bolt':  '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2L4.5 13.5H11L10 22l8.5-11.5H12z"></path></svg>',
 'chat':  '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.4" stroke-linejoin="round"><path d="M21 11.5a8 8 0 0 1-8.5 8 9 9 0 0 1-3.3-.6L4 20.5l1.6-4.6A8 8 0 0 1 4.5 11 8 8 0 0 1 13 3.5a8 8 0 0 1 8 8z"></path></svg>',
 'tag2':  '<svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20.5 13.5L13 21a2 2 0 0 1-2.8 0l-7-7A2 2 0 0 1 2.6 12.6V5a2 2 0 0 1 2-2h7.6a2 2 0 0 1 1.4.6l6.9 6.9a2 2 0 0 1 0 2.8z"></path><circle cx="8" cy="8" r="1.6"></circle></svg>',
})

PAPER = '#FAF6F4'
RED   = '#E8352B'

FRAMES = [
 dict(out='Main.dc.html', screen='home', bg=RED, logo='spinr-logo-onred.png',
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
    html = TPL.format(bg=f['bg'], decor=f['decor'], logo=f['logo'], hcolor=f['hcolor'],
                      scolor=f['scolor'], ptop=f['ptop'], headline=f['headline'], sub=f['sub'],
                      screen=screen, screenbg='#FFFFFF', chips=chips)
    open(f['out'], 'w').write(html)
    d, dc = html.count('<div'), html.count('</div>')
    s, sc = html.count('<svg'), html.count('</svg>')
    print(f"{f['out']:16} screen={f['screen']:9} div {d}/{dc} svg {s}/{sc} {'OK' if d==dc and s==sc else 'MISMATCH'}")
