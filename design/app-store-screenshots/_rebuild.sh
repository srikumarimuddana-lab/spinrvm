#!/bin/sh
python3 - <<'PY'
import json, re, base64
fonts=open('_fonts.css').read()
imgs={n:'data:image/png;base64,'+base64.b64encode(open(n,'rb').read()).decode() for n in ('spinr-logo.png','spinr-logo-light.png','spinr-logo-onred.png')}
sizes=json.load(open('_render/sizes.json'))
TPL="""<!doctype html><html><head><meta charset="utf-8"><style>{fonts}</style>
<style>html,body{{margin:0;padding:0;background:#0E1013;}}{css}</style></head><body>{body}</body></html>"""
for name in sizes:
    src=open(name+'.dc.html').read()
    helmet=re.search(r'<helmet>(.*?)</helmet>',src,re.S).group(1)
    css='\n'.join(re.findall(r'<style>(.*?)</style>',helmet,re.S))
    body=re.search(r'</helmet>(.*?)</x-dc>',src,re.S).group(1).strip()
    for n,u in imgs.items(): body=body.replace('src="%s"'%n,'src="%s"'%u)
    open('_render/%s.html'%name,'w').write(TPL.format(fonts=fonts,css=css,body=body))
PY
