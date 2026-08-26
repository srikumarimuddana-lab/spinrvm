"""Generate all five phone screens from real app structure + real data."""
import os
import _gmap as M

# ---- side-view vehicle art in the app's own car-art palette (carImage.ts) ----
def _car_side(kind):
    if kind == 'van':
        body = '<path d="M14 58 L16 40 Q17 26 32 24 L118 20 Q140 20 150 31 L161 43 Q164 47 164 52 L163 58 Z" fill="url(#cb)" stroke="#C0C0C4" stroke-width="1.4"/>' \
               '<path d="M34 38 L36 29 Q37 26 42 26 L58 25 L58 38 Z" fill="url(#cg)"/>' \
               '<rect x="64" y="25" width="38" height="13" rx="3" fill="url(#cg)"/>' \
               '<rect x="108" y="25" width="34" height="13" rx="3" fill="url(#cg)"/>' \
               '<line x1="62" y1="24" x2="62" y2="54" stroke="#C4C4C8" stroke-width="1.6"/>' \
               '<line x1="105" y1="24" x2="105" y2="50" stroke="#C4C4C8" stroke-width="1.6"/>'
        wheels = [(44, 60), (134, 60)]
    elif kind == 'xl':
        body = '<path d="M16 58 L20 34 Q22 22 36 20 L118 18 Q136 18 146 30 L160 42 Q164 46 164 52 L164 58 Z" fill="url(#cb)" stroke="#C0C0C4" stroke-width="1.4"/>' \
               '<path d="M40 34 Q42 26 52 25 L110 24 Q122 24 130 32 L136 38 L40 38 Z" fill="url(#cg)"/>' \
               '<rect x="30" y="16" width="104" height="4" rx="2" fill="#C8C8CC"/>'
        wheels = [(48, 60), (136, 60)]
    else:  # sedan (Economy)
        body = '<path d="M14 56 L20 40 Q24 34 34 33 L52 22 Q56 18 64 18 L108 18 Q116 18 122 24 L136 34 Q152 36 158 44 L162 52 Q164 56 160 58 L16 58 Z" fill="url(#cb)" stroke="#C0C0C4" stroke-width="1.4"/>' \
               '<path d="M58 33 L64 24 Q66 22 70 22 L104 22 Q110 22 114 26 L124 33 Z" fill="url(#cg)"/>'
        wheels = [(46, 58), (132, 58)]
    w = ''.join(f'<circle cx="{x}" cy="{y}" r="13" fill="#2D2D3A"/><circle cx="{x}" cy="{y}" r="6" fill="#AAAAB0"/>' for x, y in wheels)
    return (f'<svg width="178" height="80" viewBox="0 0 178 80">'
            f'<defs><linearGradient id="cb" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="#F8F8FA"/><stop offset="100%" stop-color="#D9D9DE"/></linearGradient>'
            f'<linearGradient id="cg" x1="0" y1="0" x2="0" y2="1">'
            f'<stop offset="0%" stop-color="#3B8DB5"/><stop offset="100%" stop-color="#62B4D8"/></linearGradient></defs>'
            f'<ellipse cx="89" cy="70" rx="72" ry="7" fill="rgba(0,0,0,0.10)"/>{body}{w}</svg>')

def vehicle_art(kind, w=320, h=209, scale=1.0):
    """Real Supabase illustration when veh-<kind>.webp is present. Fixed layout
    box like the app's carImageContainer (150x98pt); unselected rows render the
    image at transform-scale 0.59 inside the same box. Drawn fallback otherwise."""
    import base64, os
    f = f'veh-{kind}.webp'
    iw, ih = round(w * scale), round(h * scale)
    inner = None
    if os.path.exists(f):
        b = base64.b64encode(open(f, 'rb').read()).decode()
        inner = f'<img src="data:image/webp;base64,{b}" style="width: {iw}px; height: {ih}px; object-fit: contain;">'
    else:
        inner = _car_side(kind)
    return (f'<div style="flex-shrink: 0; width: {w}px; height: {h}px; display: flex; align-items: center; justify-content: center;">'
            f'{inner}</div>')

# ------------------------------- home ---------------------------------------
def home():
    m = M.base_map(830, 1160)
    cars = M.car(275, 410, 0) + M.car(640, 736, -90) + M.car(130, 950, 180)
    dot = M.blue_dot(430, 620)
    return f'''      <div style="position: relative; flex-grow: 1; background: {M.LAND};">
        <svg width="830" height="1160" viewBox="0 0 830 1160" style="position: absolute; inset: 0; display: block;">
{m}
{cars}
{dot}
        </svg>
        <div style="position: absolute; top: 128px; right: 26px; display: flex; flex-direction: column; gap: 18px;">
          <div style="width: 94px; height: 94px; border-radius: 47px; background: #FFFFFF; display: flex; align-items: center; justify-content: center; box-shadow: 0 4px 14px rgba(0,0,0,0.14);">
            <svg width="42" height="42" viewBox="0 0 24 24" fill="none" stroke="#1A1A1A" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="3.2"></circle><path d="M12 2v3.2M12 18.8V22M22 12h-3.2M5.2 12H2"></path><circle cx="12" cy="12" r="8"></circle></svg>
          </div>
        </div>
      </div>

      <div style="flex-shrink: 0; background: #FFFFFF; border-radius: 51px 51px 0 0; padding: 26px 43px 43px; box-shadow: 0 -4px 24px rgba(0,0,0,0.10);">
        <div style="width: 85px; height: 9px; border-radius: 5px; background: #E5E7EB; margin: 0 auto 30px;"></div>
        <div style="display: flex; align-items: center; gap: 21px; margin-bottom: 43px;">
          <div style="flex-grow: 1; display: flex; align-items: center; gap: 26px; background: #F5F5F5; border-radius: 60px; padding: 34px 43px;">
            <svg width="47" height="47" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"></circle><path d="M20 20l-4-4"></path></svg>
            <span style="font-size: 34px; font-weight: 500; color: #666666;">Where to?</span>
          </div>
          <div style="width: 119px; height: 119px; border-radius: 60px; background: #FF3B30; display: flex; flex-direction: column; align-items: center; justify-content: center; box-shadow: 0 8px 17px rgba(255,59,48,0.35);">
            <svg width="43" height="43" viewBox="0 0 24 24" fill="#FFFFFF"><path d="M12 2l2.1 5.6L20 9.5l-5.9 1.9L12 17l-2.1-5.6L4 9.5l5.9-1.9z"></path></svg>
            <span style="font-size: 21px; font-weight: 800; letter-spacing: 0.05em; color: #FFFFFF; margin-top: 2px;">AI</span>
          </div>
        </div>
        <div style="display: flex; justify-content: space-around; margin-bottom: 43px;">
          <div style="display: flex; flex-direction: column; align-items: center;">
            <div style="width: 119px; height: 119px; border-radius: 60px; background: #FFF0F0; display: flex; align-items: center; justify-content: center; margin-bottom: 17px;">
              <svg width="47" height="47" viewBox="0 0 24 24" fill="#FF3B30"><path d="M12 3.2L3 10.4V21h6v-6h6v6h6V10.4z"></path></svg>
            </div>
            <span style="font-size: 28px; font-weight: 500; color: #1A1A1A;">Home</span>
          </div>
          <div style="display: flex; flex-direction: column; align-items: center;">
            <div style="width: 119px; height: 119px; border-radius: 60px; background: #FFF0F0; display: flex; align-items: center; justify-content: center; margin-bottom: 17px;">
              <svg width="47" height="47" viewBox="0 0 24 24" fill="#FF3B30"><path d="M9 4h6a2 2 0 0 1 2 2v2h3a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2h3V6a2 2 0 0 1 2-2zm0 4h6V6H9z"></path></svg>
            </div>
            <span style="font-size: 28px; font-weight: 500; color: #1A1A1A;">Work</span>
          </div>
          <div style="display: flex; flex-direction: column; align-items: center;">
            <div style="width: 119px; height: 119px; border-radius: 60px; background: #FFF0F0; display: flex; align-items: center; justify-content: center; margin-bottom: 17px;">
              <svg width="47" height="47" viewBox="0 0 24 24" fill="#FF3B30"><path d="M12 3l2.7 5.8 6.3.8-4.6 4.4 1.2 6.2L12 17.3 6.4 20.2l1.2-6.2L3 9.6l6.3-.8z"></path></svg>
            </div>
            <span style="font-size: 28px; font-weight: 500; color: #1A1A1A;">Saved</span>
          </div>
        </div>
        <div style="display: flex; align-items: center; gap: 26px; background: #FFF0F0; border-radius: 34px; padding: 30px 34px;">
          <div style="flex-shrink: 0; width: 85px; height: 85px; border-radius: 43px; background: #FFFFFF; display: flex; align-items: center; justify-content: center;">
            <svg width="43" height="43" viewBox="0 0 24 24" fill="#FF3B30"><path d="M4 9h3l8-4.5v15L7 15H4a2 2 0 0 1-2-2v-2a2 2 0 0 1 2-2zm13-1.6a5 5 0 0 1 0 9.2z"></path></svg>
          </div>
          <div style="display: flex; flex-direction: column; gap: 6px;">
            <span style="font-size: 30px; font-weight: 700; color: #1A1A1A;">Ride local. Support local.</span>
            <span style="font-size: 26px; font-weight: 500; line-height: 1.35; color: #6B7280;">Spinr takes no cut &mdash; 100% of<br>your fare goes to your driver.</span>
          </div>
        </div>
      </div>'''

# ------------------------------ options -------------------------------------
def options():
    def price(orig, disc):
        return ('<div style="display: flex; flex-direction: column; align-items: flex-end; gap: 2px;">'
                f'<span style="font-size: 26px; font-weight: 500; color: #6B7280; text-decoration: line-through;">${orig}</span>'
                f'<span style="font-size: 37px; font-weight: 800; color: #10B981;">${disc}</span></div>')

    def row(kind, name, cap, meta_line, orig, disc, selected=False):
        border = 'border: 3px solid #1A1A1A;' if selected else 'border: 2px solid #E5E7EB;'
        check = ('<svg width="43" height="43" viewBox="0 0 24 24" fill="#1A1A1A" style="margin-top: 6px;">'
                 '<path d="M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20zm-1.2 14.5L6.6 12.3l1.7-1.7 2.5 2.5 5-5 1.7 1.7z"></path></svg>') if selected else ''
        art = vehicle_art(kind, 320, 209, 1.0 if selected else 0.59)
        return (
f'''        <div style="display: flex; align-items: center; gap: 22px; padding: 20px 30px; border-radius: 30px; background: #FFFFFF; {border}">
          {art}
          <div style="flex-grow: 1; display: flex; flex-direction: column; gap: 8px;">
            <div style="display: flex; align-items: center; gap: 14px;">
              <span style="font-size: 34px; font-weight: 700; color: #1A1A1A;">{name}</span>
              <div style="display: flex; align-items: center; gap: 7px; padding: 5px 14px; border-radius: 16px; background: #F5F5F5;">
                <svg width="22" height="22" viewBox="0 0 24 24" fill="#666666"><circle cx="12" cy="8" r="4"></circle><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7z"></path></svg>
                <span style="font-size: 24px; font-weight: 600; color: #666666;">{cap}</span>
              </div>
            </div>
            <span style="font-size: 26px; font-weight: 500; color: #6B7280;">{meta_line}</span>
          </div>
          <div style="display: flex; flex-direction: column; align-items: flex-end;">
            {price(orig, disc)}
            {check}
          </div>
        </div>''')

    rows = '\n'.join([
        row('sedan', 'Economy', 4, '3 min &middot; 5 drivers', '22.99', '5.75', selected=True),
        row('van', 'Van', 6, '6 min &middot; 2 drivers', '29.75', '7.44'),
        row('xl', 'XL', 6, '8 min &middot; 2 drivers', '31.40', '7.85'),
    ])
    return (
f'''      <div style="flex-shrink: 0; display: flex; align-items: center; gap: 26px; padding: 150px 43px 26px; background: #FFFFFF;">
        <div style="width: 90px; height: 90px; border-radius: 45px; background: #F5F5F5; display: flex; align-items: center; justify-content: center;">
          <svg width="42" height="42" viewBox="0 0 24 24" fill="none" stroke="#1A1A1A" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"></path></svg>
        </div>
        <span style="font-size: 42px; font-weight: 800; letter-spacing: -0.02em; color: #1A1A1A;">Choose a ride</span>
      </div>
      <div style="flex-shrink: 0; padding: 14px 43px 30px; background: #FFFFFF; display: flex; gap: 26px;">
        <div style="flex-shrink: 0; display: flex; flex-direction: column; align-items: center; padding: 10px 0;">
          <div style="width: 21px; height: 21px; border-radius: 11px; background: #10B981; border: 4.5px solid #FFFFFF; box-shadow: 0 0 0 2px #10B981;"></div>
          <div style="width: 4px; flex-grow: 1; min-height: 44px; background: #E5E7EB;"></div>
          <div style="width: 21px; height: 21px; background: #EF4444; border: 4.5px solid #FFFFFF; box-shadow: 0 0 0 2px #EF4444;"></div>
        </div>
        <div style="flex-grow: 1; display: flex; flex-direction: column; justify-content: space-between; gap: 26px;">
          <span style="font-size: 30px; font-weight: 600; color: #1A1A1A;">Broadway Ave &amp; 8th St E</span>
          <span style="font-size: 30px; font-weight: 600; color: #1A1A1A;">Midtown Plaza</span>
        </div>
      </div>
      <div style="flex-grow: 1; padding: 26px 43px 0; background: #F5F5F5; display: flex; flex-direction: column; gap: 20px;">
{rows}
        <div style="display: flex; align-items: center; gap: 24px; padding: 24px 30px; border-radius: 30px; background: #ECFDF5; border: 2px solid #A7F3D0;">
          <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#059669" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M20.5 13.5L13 21a2 2 0 0 1-2.8 0l-7-7A2 2 0 0 1 2.6 12.6V5a2 2 0 0 1 2-2h7.6a2 2 0 0 1 1.4.6l6.9 6.9a2 2 0 0 1 0 2.8z"></path><circle cx="8" cy="8" r="1.6"></circle></svg>
          <div style="flex-grow: 1; display: flex; flex-direction: column; gap: 3px;">
            <span style="font-size: 30px; font-weight: 700; color: #059669;">WELCOME75 applied</span>
            <span style="font-size: 25px; font-weight: 500; color: #3F7A5B;">75% off your first ride</span>
          </div>
          <span style="font-size: 27px; font-weight: 600; color: #DC2626;">Remove</span>
        </div>
      </div>
      <div style="flex-shrink: 0; padding: 26px 43px 46px; background: #F5F5F5;">
        <div style="height: 119px; border-radius: 60px; background: #FF3B30; display: flex; align-items: center; justify-content: center; box-shadow: 0 10px 26px rgba(255,59,48,0.32);">
          <span style="font-size: 34px; font-weight: 600; color: #FFFFFF;">Confirm Economy &middot; $5.75</span>
        </div>
      </div>
''')
# ------------------------------ tracking ------------------------------------
def tracking():
    m = M.base_map(830, 1200)
    route_pts = [(640, 1010), (505, 1010), (505, 534), (275, 534), (275, 300)]
    route = M.gradient_route(route_pts)
    car = M.car(640, 1010, -90)
    p = M.pin(275, 300, 'pickup')
    return f'''      <div style="position: relative; flex-grow: 1; background: {M.LAND};">
        <svg width="830" height="1200" viewBox="0 0 830 1200" style="position: absolute; inset: 0; display: block;">
{m}
{route}
{p}
{car}
        </svg>
        <div style="position: absolute; top: 128px; left: 26px; display: flex; align-items: center; gap: 14px; padding: 18px 30px; border-radius: 999px; background: #FFFFFF; box-shadow: 0 4px 16px rgba(0,0,0,0.14);">
          <span style="width: 18px; height: 18px; border-radius: 9px; background: #34C759;"></span>
          <span style="font-size: 27px; font-weight: 700; color: #1A1A1A;">On the way</span>
        </div>
      </div>
      <div style="flex-shrink: 0; background: #FFFFFF; border-radius: 51px 51px 0 0; padding: 26px 43px 43px; box-shadow: 0 -4px 24px rgba(0,0,0,0.10);">
        <div style="width: 85px; height: 9px; border-radius: 5px; background: #E5E7EB; margin: 0 auto 30px;"></div>
        <span style="display: block; font-size: 52px; font-weight: 800; letter-spacing: -0.025em; color: #1A1A1A; margin-bottom: 6px;">Arriving in 3 min</span>
        <span style="display: block; font-size: 28px; font-weight: 500; color: #6B7280; margin-bottom: 32px;">Meet at Broadway Ave &amp; 8th St E</span>
        <div style="display: flex; align-items: center; gap: 26px; padding: 30px 34px; border-radius: 34px; background: #F5F5F5; margin-bottom: 26px;">
          <div style="flex-shrink: 0; width: 110px; height: 110px; border-radius: 55px; background: #FFFFFF; display: flex; align-items: center; justify-content: center;">
            <svg width="56" height="56" viewBox="0 0 24 24" fill="#9CA3AF"><circle cx="12" cy="8" r="4"></circle><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7z"></path></svg>
          </div>
          <div style="flex-grow: 1; display: flex; flex-direction: column; gap: 7px;">
            <div style="display: flex; align-items: center; gap: 12px;">
              <span style="font-size: 34px; font-weight: 700; color: #1A1A1A;">Sam R.</span>
              <svg width="28" height="28" viewBox="0 0 24 24" fill="#FFD700"><path d="M12 2l3 6.3 6.8.9-5 4.8 1.3 6.8L12 17.5 5.9 20.8 7.2 14l-5-4.8 6.8-.9z"></path></svg>
              <span style="font-size: 28px; font-weight: 600; color: #6B7280;">4.9</span>
            </div>
            <span style="font-size: 27px; font-weight: 500; color: #6B7280;">Grey Toyota Corolla</span>
          </div>
          <div style="flex-shrink: 0; padding: 12px 22px; border-radius: 14px; background: #FFFFFF; border: 2px solid #E5E7EB;">
            <span style="font-size: 28px; font-weight: 800; letter-spacing: 0.06em; color: #1A1A1A;">4WX 812</span>
          </div>
        </div>
        <div style="display: flex; gap: 20px;">
          <div style="flex-grow: 1; height: 110px; border-radius: 55px; background: #F5F5F5; display: flex; align-items: center; justify-content: center; gap: 14px;">
            <svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#1A1A1A" stroke-width="2.2" stroke-linejoin="round"><path d="M6.5 3.5h3l2 4.5-2.2 1.6a12 12 0 0 0 5.1 5.1L16 12.5l4.5 2v3a2 2 0 0 1-2.2 2A16.5 16.5 0 0 1 4.5 5.7a2 2 0 0 1 2-2.2z"></path></svg>
            <span style="font-size: 29px; font-weight: 600; color: #1A1A1A;">Call</span>
          </div>
          <div style="flex-grow: 1; height: 110px; border-radius: 55px; background: #F5F5F5; display: flex; align-items: center; justify-content: center; gap: 14px;">
            <svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#1A1A1A" stroke-width="2.2" stroke-linejoin="round"><path d="M21 11.5a8 8 0 0 1-8.5 8 9 9 0 0 1-3.3-.6L4 20.5l1.6-4.6A8 8 0 0 1 4.5 11 8 8 0 0 1 13 3.5a8 8 0 0 1 8 8z"></path></svg>
            <span style="font-size: 29px; font-weight: 600; color: #1A1A1A;">Message</span>
          </div>
          <div style="flex-shrink: 0; width: 110px; height: 110px; border-radius: 55px; background: #FF3B30; display: flex; align-items: center; justify-content: center; box-shadow: 0 8px 20px rgba(255,59,48,0.32);">
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2.5l8 3.2v6c0 5-3.4 8.8-8 10-4.6-1.2-8-5-8-10v-6z"></path></svg>
          </div>
        </div>
      </div>'''

# --------------------------------- ai ---------------------------------------
def ai():
    prompts = ["Where's my driver?", 'Explain my last fare', "What's my wallet balance?", 'Do I have any promos?', 'Book me a ride home']
    chips = '\n'.join(
        f'          <div style="padding: 22px 34px; border-radius: 40px; background: #F5F5F5; border: 2px solid #E5E7EB;"><span style="font-size: 28px; font-weight: 600; color: #1A1A1A;">{p}</span></div>'
        for p in prompts)
    return f'''      <div style="flex-shrink: 0; display: flex; align-items: center; gap: 22px; padding: 150px 43px 26px;">
        <svg width="42" height="42" viewBox="0 0 24 24" fill="none" stroke="#1A1A1A" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"></path></svg>
        <div style="display: flex; align-items: center; gap: 12px;">
          <svg width="34" height="34" viewBox="0 0 24 24" fill="#FF3B30"><path d="M12 2l2.1 5.6L20 9.5l-5.9 1.9L12 17l-2.1-5.6L4 9.5l5.9-1.9z"></path></svg>
          <span style="font-size: 36px; font-weight: 600; color: #1A1A1A;">Spinr Assistant</span>
        </div>
      </div>
      <div style="flex-grow: 1; display: flex; flex-direction: column; align-items: center; justify-content: center; padding: 0 51px; text-align: center;">
        <div style="position: relative; width: 220px; height: 220px; display: flex; align-items: center; justify-content: center; margin-bottom: 40px;">
          <div style="position: absolute; inset: 0; border-radius: 110px; background: rgba(255,59,48,0.16); filter: blur(18px);"></div>
          <div style="position: relative; width: 172px; height: 172px; border-radius: 86px; background: linear-gradient(135deg, #FF3B30 0%, #FF3B30 34%, #FF9500 82%, #FFFFFF 140%); display: flex; align-items: center; justify-content: center; box-shadow: 0 16px 40px rgba(255,59,48,0.35);">
            <svg width="64" height="64" viewBox="0 0 24 24" fill="#FFFFFF"><path d="M12 2l2.1 5.6L20 9.5l-5.9 1.9L12 17l-2.1-5.6L4 9.5l5.9-1.9zM19 15l1 2.6 2.6 1-2.6 1L19 22l-1-2.4-2.6-1 2.6-1z"></path></svg>
          </div>
        </div>
        <span style="font-size: 46px; font-weight: 800; letter-spacing: -0.02em; color: #1A1A1A;">Hi Sam, let&rsquo;s get going</span>
        <span style="margin-top: 18px; max-width: 640px; font-size: 28px; line-height: 1.45; font-weight: 500; color: #6B7280;">Ask about your rides, fares, wallet or promos &mdash; or ask me to get you a ride quote.</span>
        <div style="display: flex; flex-wrap: wrap; justify-content: center; gap: 18px; margin-top: 46px;">
{chips}
        </div>
      </div>
      <div style="flex-shrink: 0; display: flex; align-items: center; gap: 20px; padding: 26px 43px 46px;">
        <div style="flex-grow: 1; display: flex; align-items: center; background: #F5F5F5; border-radius: 55px; padding: 32px 43px;">
          <span style="font-size: 32px; font-weight: 500; color: #666666;">Ask me anything&hellip;</span>
        </div>
        <div style="flex-shrink: 0; width: 110px; height: 110px; border-radius: 55px; background: #FF3B30; display: flex; align-items: center; justify-content: center; box-shadow: 0 8px 20px rgba(255,59,48,0.32);">
          <svg width="44" height="44" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 19V5M5 12l7-7 7 7"></path></svg>
        </div>
      </div>'''

# ------------------------------- support ------------------------------------
def support():
    faqs = ['How do I book a ride?', 'How is my fare calculated?', 'What is surge pricing and when does it apply?',
            'Can I schedule a ride for later?', 'How do I top up my Spinr wallet?', 'How do I use a promo code?']
    rows = []
    for i, q in enumerate(faqs):
        if i == 1:
            rows.append(f'''        <div style="padding: 30px 34px; border-radius: 30px; background: #F5F5F5;">
          <div style="display: flex; align-items: center; justify-content: space-between; gap: 20px;">
            <span style="font-size: 30px; font-weight: 700; color: #1A1A1A;">{q}</span>
            <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#FF3B30" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M18 15l-6-6-6 6"></path></svg>
          </div>
          <span style="display: block; margin-top: 16px; font-size: 26px; line-height: 1.45; font-weight: 500; color: #6B7280;">A base fare plus per-kilometre and per-minute rates for your city &mdash; shown upfront before you book, with GST and PST as separate line items.</span>
        </div>''')
        else:
            rows.append(f'''        <div style="display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 30px 34px; border-radius: 30px; background: #F5F5F5;">
          <span style="font-size: 30px; font-weight: 700; color: #1A1A1A;">{q}</span>
          <svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"></path></svg>
        </div>''')
    rows = '\n'.join(rows)
    return f'''      <div style="flex-shrink: 0; display: flex; align-items: center; gap: 22px; padding: 150px 43px 26px;">
        <svg width="42" height="42" viewBox="0 0 24 24" fill="none" stroke="#1A1A1A" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M15 5l-7 7 7 7"></path></svg>
        <span style="font-size: 38px; font-weight: 600; color: #1A1A1A;">Help &amp; Support</span>
      </div>
      <div style="flex-shrink: 0; display: flex; gap: 14px; padding: 0 43px 26px;">
        <div style="flex-grow: 1; display: flex; align-items: center; justify-content: center; gap: 12px; height: 92px; border-radius: 46px; background: #FF3B30;">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"></circle><path d="M9.2 9a2.8 2.8 0 0 1 5.4 1c0 1.8-2.6 2.2-2.6 3.6"></path><path d="M12 17.2h.01"></path></svg>
          <span style="font-size: 28px; font-weight: 700; color: #FFFFFF;">FAQ</span>
        </div>
        <div style="flex-grow: 1; display: flex; align-items: center; justify-content: center; gap: 12px; height: 92px; border-radius: 46px; background: #F5F5F5;">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="#6B7280"><path d="M12 2l2.1 5.6L20 9.5l-5.9 1.9L12 17l-2.1-5.6L4 9.5l5.9-1.9z"></path></svg>
          <span style="font-size: 28px; font-weight: 600; color: #6B7280;">AI Chat</span>
        </div>
        <div style="flex-grow: 1; display: flex; align-items: center; justify-content: center; gap: 12px; height: 92px; border-radius: 46px; background: #F5F5F5;">
          <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="#6B7280" stroke-width="2.2" stroke-linejoin="round"><rect x="2.5" y="5" width="19" height="14" rx="3"></rect><path d="M3 7l9 6 9-6"></path></svg>
          <span style="font-size: 28px; font-weight: 600; color: #6B7280;">Contact</span>
        </div>
      </div>
      <div style="flex-shrink: 0; display: flex; align-items: center; gap: 22px; margin: 0 43px 26px; padding: 28px 38px; border-radius: 50px; background: #F5F5F5;">
        <svg width="38" height="38" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="2.2" stroke-linecap="round"><circle cx="11" cy="11" r="7"></circle><path d="M20 20l-4-4"></path></svg>
        <span style="font-size: 30px; font-weight: 500; color: #9CA3AF;">Search questions...</span>
      </div>
      <div style="flex-grow: 1; padding: 0 43px; display: flex; flex-direction: column; gap: 18px;">
{rows}
      </div>
      <div style="flex-shrink: 0; height: 40px;"></div>'''

if __name__ == '__main__':
    for name, fn in [('home', home), ('options', options), ('tracking', tracking), ('ai', ai), ('support', support)]:
        s = fn()
        open(f'_screens/{name}.html', 'w').write(s)
        ok = s.count('<div') == s.count('</div>') and s.count('<svg') == s.count('</svg>')
        print(f"{name:9} div {s.count('<div')}/{s.count('</div>')} svg {s.count('<svg')}/{s.count('</svg>')} {'OK' if ok else 'MISMATCH'}")

