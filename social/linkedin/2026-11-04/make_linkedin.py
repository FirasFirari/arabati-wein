"""Render the LinkedIn carousel (1080x1350 slides + a PDF) from the growth CSVs.
Aggregates only: no chassis numbers, plates, contacts or watch identifiers."""
import base64, csv, json, pathlib, sys
from PIL import Image
from playwright.sync_api import sync_playwright

HERE = pathlib.Path(__file__).resolve().parent
ROOT = pathlib.Path(r"C:\Users\User\Desktop\Car Hunt")
OUT = ROOT / "social" / "linkedin" / "2026-11-04"
OUT.mkdir(parents=True, exist_ok=True)
LOGO = "data:image/png;base64," + base64.b64encode((ROOT / "funding" / "arabati-wein-logo.png").read_bytes()).decode()
W, H = 1080, 1350
N = 7

def rows(name):
    with open(HERE / name, encoding="utf-8") as f:
        return list(csv.DictReader(f))

reg = rows("registry_daily.csv")
wat = rows("watches_daily.csv")
src = rows("traffic_sources.csv")
snap = {r["metric"]: int(r["value"]) for r in rows("snapshot.csv")}
# one moment for every slide: the daily file's last running totals
snap["posts_collected"] = int(reg[-1]["posts_total"])
snap["distinct_chassis"] = int(reg[-1]["chassis_total"])
F = lambda n: f"{n:,}"

DATA = json.dumps({
    "reg": [{"d": r["date"], "p": int(r["posts_total"]), "c": int(r["chassis_total"]), "pn": int(r["posts_new"])} for r in reg],
    "wat": [{"d": r["date"], "w": int(r["watches_total"])} for r in wat],
    "src": [{"w": r["window_end"], "s": r["source"], "n": int(r["sessions"])} for r in src],
})

FONTS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700"
         "&family=IBM+Plex+Sans+Arabic:wght@500;700&family=Amiri:wght@700&display=swap")

CSS = """
:root{--paper:#F8F4EC;--ink:#10282E;--muted:#55676B;--grid:#E4DDCF;--axis:#B3AA99;
 --teal:#00849C;--amber:#A66C00;--purple:#6B4FA0;--rust:#C2553A;--blue:#2F6FD0;
 --deep:#0F4C5C;--deep2:#146C7C;--gold:#F0B23C;--mist:#D6E8EC}
*{box-sizing:border-box}
html,body{margin:0;width:1080px;height:1350px;overflow:hidden}
body{font-family:"IBM Plex Sans","Segoe UI",sans-serif;color:var(--ink);background:var(--paper);
 display:flex;flex-direction:column;padding:72px 84px 64px}
body.dark{background:linear-gradient(160deg,#0F4C5C 0%,#146C7C 70%,#1A7F8E 100%);color:#fff}
.top{display:flex;align-items:center;gap:18px;font-size:26px;font-weight:500;color:var(--muted)}
.dark .top{color:var(--mist)}
.top img{width:56px;height:56px;border-radius:14px}
.ar{font-family:"IBM Plex Sans Arabic",sans-serif;font-weight:700}
h1{font-size:60px;line-height:1.08;font-weight:600;margin:56px 0 0;letter-spacing:-.5px;text-wrap:balance}
.sub{font-size:29px;line-height:1.4;color:var(--muted);margin:18px 0 0;max-width:880px}
.dark .sub{color:var(--mist)}
.main{flex:1;display:flex;flex-direction:column;justify-content:center;min-height:0}
.foot{display:flex;justify-content:space-between;align-items:flex-end;font-size:24px;color:var(--muted)}
.dark .foot{color:var(--mist)}
.foot b{color:var(--ink);font-weight:600}
.dark .foot b{color:var(--gold)}
.src{font-size:21px;color:var(--muted);margin:0 0 18px}
.legend{display:flex;flex-wrap:wrap;gap:10px 28px;font-size:25px;color:var(--muted);margin:30px 0 6px}
.legend span{display:inline-flex;align-items:center;gap:10px}
.sw{width:22px;height:22px;border-radius:5px;display:inline-block}
svg text{font-family:"IBM Plex Sans",sans-serif}
.tick{fill:var(--muted);font-size:23px}
.num{font-variant-numeric:tabular-nums}
"""

def page(body, n, dark=False, script=""):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<link rel="stylesheet" href="{FONTS}">
<script src="https://cdnjs.cloudflare.com/ajax/libs/d3/7.8.5/d3.min.js"></script>
<style>{CSS}</style></head>
<body class="{'dark' if dark else ''}">
<div class="top"><img src="{LOGO}" alt=""><span>Arabati Wein · <bdi class="ar" dir="rtl" lang="ar">عربتي وين؟</bdi></span></div>
{body}
<div class="foot"><span><b>mycarsd.online</b></span><span class="num">{n}/{N}</span></div>
<script>const DATA={DATA};{script}</script>
</body></html>"""

# ---------------------------------------------------------------- slide 1
s1 = page(f"""
<div class="main" style="justify-content:flex-start;padding-top:70px">
  <p style="font-size:38px;line-height:1.35;margin:0;color:var(--mist);max-width:860px">75 days of a free registry for cars looted in the Sudan war</p>
  <div class="num" style="font-size:210px;font-weight:700;line-height:1;margin:64px 0 0;color:var(--gold);letter-spacing:-4px">{F(snap['distinct_chassis'])}</div>
  <p style="font-size:40px;margin:18px 0 0;font-weight:500">distinct chassis numbers anyone can search</p>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:44px 40px;margin-top:84px">
    <div><div class="num" style="font-size:66px;font-weight:600">{F(snap['posts_collected'])}</div><div style="font-size:27px;color:var(--mist)">public posts collected</div></div>
    <div><div class="num" style="font-size:66px;font-weight:600">{F(snap['identified_vehicles'])}</div><div style="font-size:27px;color:var(--mist)">vehicles identified</div></div>
    <div><div class="num" style="font-size:66px;font-weight:600">{F(snap['photos_transcribed'])}</div><div style="font-size:27px;color:var(--mist)">photos read by OCR</div></div>
    <div><div class="num" style="font-size:66px;font-weight:600">{F(snap['standing_watches'])}</div><div style="font-size:27px;color:var(--mist)">numbers on watch</div></div>
  </div>
</div>
<p class="src" style="color:var(--mist)">22 August to 4 November 2026</p>""", 1, dark=True)

# ---------------------------------------------------------------- slide 2
MS = [("2026-08-29", "Photos become searchable text (OCR)"),
      ("2026-09-01", "A parser for long found-vehicle lists"),
      ("2026-10-13", "Reposts merged, search gets fast"),
      ("2026-10-28", "On Google, Bing and a Facebook Page")]
ms_html = "".join(
    f'<div style="display:flex;gap:18px;align-items:baseline;font-size:26px"><span class="num" style="display:inline-flex;width:38px;height:38px;border-radius:50%;background:var(--ink);color:var(--paper);font-size:22px;font-weight:600;align-items:center;justify-content:center;flex:none">{i+1}</span>'
    f'<span class="num" style="width:110px;color:var(--muted);flex:none">{"Aug 29" if d.endswith("08-29") else "Sep 1" if d.endswith("09-01") else "Oct 13" if d.endswith("10-13") else "Oct 28"}</span><span>{t}</span></div>'
    for i, (d, t) in enumerate(MS))
s2 = page(f"""
<h1>Growth, day by day</h1>
<p class="sub">Running totals since the first post on 22 August. A chassis number counts on the day it is first read out of a post.</p>
<div class="legend"><span><i class="sw" style="background:var(--teal)"></i>Posts collected</span><span><i class="sw" style="background:var(--amber)"></i>Distinct chassis numbers</span></div>
<div class="main"><svg id="c" width="912" height="600"></svg></div>
<div style="display:grid;gap:12px;margin:8px 0 26px">{ms_html}</div>""", 2, script=f"""
const ms={json.dumps([d for d,_ in MS])};
const W=912,H=600,m={{l:88,r:16,t:56,b:46}};
const p=d3.utcParse('%Y-%m-%d'); const rows=DATA.reg.map(r=>({{...r,_d:p(r.d)}}));
const x=d3.scaleUtc(d3.extent(rows,r=>r._d),[m.l,W-m.r]);
const y=d3.scaleLinear([0,200000],[H-m.b,m.t]);
const s=d3.select('#c');
y.ticks(4).forEach(t=>{{s.append('line').attr('x1',m.l).attr('x2',W-m.r).attr('y1',y(t)).attr('y2',y(t)).attr('stroke','var(--grid)').attr('stroke-width',2);
 s.append('text').attr('class','tick num').attr('x',m.l-14).attr('y',y(t)).attr('dy','0.35em').attr('text-anchor','end').text(t?d3.format('~s')(t).replace('k','k'):'0');}});
s.append('line').attr('x1',m.l).attr('x2',W-m.r).attr('y1',H-m.b).attr('y2',H-m.b).attr('stroke','var(--axis)').attr('stroke-width',2);
[p('2026-09-01'),p('2026-09-15'),p('2026-10-01'),p('2026-10-15'),p('2026-11-01')].forEach(d=>s.append('text').attr('class','tick').attr('x',x(d)).attr('y',H-8).attr('text-anchor','middle').text(d3.utcFormat('%b %-d')(d)));
ms.forEach((d,i)=>{{const X=x(p(d)); s.append('line').attr('x1',X).attr('x2',X).attr('y1',m.t-6).attr('y2',H-m.b).attr('stroke','var(--axis)').attr('stroke-width',2).attr('stroke-dasharray','6 6');
 s.append('circle').attr('cx',X).attr('cy',m.t-26).attr('r',19).attr('fill','var(--ink)');
 s.append('text').attr('x',X).attr('y',m.t-26).attr('dy','0.36em').attr('text-anchor','middle').attr('fill','var(--paper)').attr('font-size',22).attr('font-weight',600).text(i+1);}});
const g=s.append('defs').append('linearGradient').attr('id','fa').attr('x1',0).attr('x2',0).attr('y1',0).attr('y2',1);
[[0,.24],[.5,.08],[1,0]].forEach(([o,a])=>g.append('stop').attr('offset',o).attr('stop-color','#00849C').attr('stop-opacity',a));
s.append('path').datum(rows).attr('fill','url(#fa)').attr('d',d3.area().x(r=>x(r._d)).y0(H-m.b).y1(r=>y(r.p)));
[['p','var(--teal)'],['c','var(--amber)']].forEach(([k,c])=>{{s.append('path').datum(rows).attr('fill','none').attr('stroke',c).attr('stroke-width',5).attr('stroke-linejoin','round').attr('stroke-linecap','round').attr('d',d3.line().x(r=>x(r._d)).y(r=>y(r[k])));
 const L=rows[rows.length-1]; s.append('circle').attr('cx',x(L._d)).attr('cy',y(L[k])).attr('r',9).attr('fill',c).attr('stroke','var(--paper)').attr('stroke-width',4);}});
const L=rows[rows.length-1];
s.append('text').attr('class','num').attr('x',x(L._d)-20).attr('y',y(L.p)-24).attr('text-anchor','end').attr('font-size',30).attr('font-weight',600).attr('fill','var(--ink)').attr('stroke','var(--paper)').attr('stroke-width',12).attr('stroke-linejoin','round').attr('paint-order','stroke').text(d3.format(',')(L.p)+' posts');
s.append('text').attr('class','num').attr('x',x(L._d)-20).attr('y',y(L.c)-24).attr('text-anchor','end').attr('font-size',30).attr('font-weight',600).attr('fill','var(--ink)').attr('stroke','var(--paper)').attr('stroke-width',12).attr('stroke-linejoin','round').attr('paint-order','stroke').text(d3.format(',')(L.c)+' chassis numbers');
""")

# ---------------------------------------------------------------- slide 3
read_share = snap["photos_transcribed"] / snap["photos_collected"]
s3 = page(f"""
<h1>The slow part is reading photos</h1>
<p class="sub">Many found-vehicle lists are published as photos of handwritten pages. A number in a photo is searchable only after OCR reads it.</p>
<div class="main" style="gap:34px">
  <div>
    <div style="font-size:27px;font-weight:600;margin-bottom:6px">Posts collected per day</div>
    <div style="font-size:23px;color:var(--muted)">Days at zero: collection was paused or the computer was off.</div>
    <svg id="b" width="912" height="330"></svg>
  </div>
  <div style="display:grid;gap:22px">
    <div style="font-size:27px;font-weight:600">Photos</div>
    <div style="display:grid;grid-template-columns:150px 1fr;align-items:center;gap:18px;font-size:26px">
      <span style="color:var(--muted)">Collected</span>
      <div style="display:flex;align-items:center;gap:16px"><div style="height:44px;width:620px;background:#CFC6B5;border-radius:0 6px 6px 0"></div><b class="num">{F(snap['photos_collected'])}</b></div>
      <span style="color:var(--muted)">Read</span>
      <div style="display:flex;align-items:center;gap:16px"><div style="height:44px;width:{round(620*read_share)}px;background:var(--teal);border-radius:0 6px 6px 0"></div><b class="num">{F(snap['photos_transcribed'])}</b></div>
    </div>
    <p style="font-size:27px;margin:8px 0 0"><b class="num">{F(snap['posts_unprocessed'])}</b> posts are still waiting to be read.</p>
  </div>
</div>""", 3, script="""
const W=912,H=330,m={l:88,r:8,t:24,b:42};
const p=d3.utcParse('%Y-%m-%d'); const rows=DATA.reg.map(r=>({...r,_d:p(r.d)}));
const x=d3.scaleBand(rows.map(r=>r.d),[m.l,W-m.r]).paddingInner(0.22);
const y=d3.scaleLinear([0,10000],[H-m.b,m.t]);
const s=d3.select('#b');
[0,5000,10000].forEach(t=>{s.append('line').attr('x1',m.l).attr('x2',W-m.r).attr('y1',y(t)).attr('y2',y(t)).attr('stroke','var(--grid)').attr('stroke-width',2);
 s.append('text').attr('class','tick num').attr('x',m.l-14).attr('y',y(t)).attr('dy','0.35em').attr('text-anchor','end').text(t?d3.format('~s')(t):'0');});
rows.forEach(r=>{if(!r.pn)return; const h=y(0)-y(r.pn); s.append('path').attr('fill','var(--teal)')
 .attr('d',`M${x(r.d)},${y(0)}V${y(r.pn)+Math.min(4,h)}q0,-${Math.min(4,h)} ${Math.min(4,h)},-${Math.min(4,h)}H${x(r.d)+x.bandwidth()-Math.min(4,h)}q${Math.min(4,h)},0 ${Math.min(4,h)},${Math.min(4,h)}V${y(0)}Z`);});
s.append('line').attr('x1',m.l).attr('x2',W-m.r).attr('y1',y(0)).attr('y2',y(0)).attr('stroke','var(--axis)').attr('stroke-width',2);
['2026-09-01','2026-10-01','2026-11-01'].forEach(d=>s.append('text').attr('class','tick').attr('x',x(d)+x.bandwidth()/2).attr('y',H-6).attr('text-anchor','middle').text(d3.utcFormat('%b %-d')(p(d))));
""")

# ---------------------------------------------------------------- slide 4
s4 = page(f"""
<h1>{F(snap['standing_watches'])} numbers on watch</h1>
<p class="sub">When a family can't find their car's number yet, they register it. Every new post is checked against it, and a match is verified before anyone is told.</p>
<div class="main"><svg id="w" width="912" height="470"></svg></div>
<div style="display:grid;grid-template-columns:1fr 1fr;gap:28px;margin-bottom:34px">
  <div style="background:#fff;border:2px solid var(--grid);border-radius:18px;padding:28px 30px">
    <div class="num" style="font-size:96px;font-weight:700;line-height:1;color:var(--teal)">{snap['leads_relayed']}</div>
    <div style="font-size:27px;margin-top:12px">verified leads passed to the families who registered</div></div>
  <div style="background:#fff;border:2px solid var(--grid);border-radius:18px;padding:28px 30px">
    <div class="num" style="font-size:96px;font-weight:700;line-height:1;color:var(--ink)">{snap['recoveries_confirmed']}</div>
    <div style="font-size:27px;margin-top:12px">recoveries confirmed so far</div></div>
</div>""", 4, script="""
const W=912,H=470,m={l:70,r:16,t:30,b:46};
const p=d3.utcParse('%Y-%m-%d'); const rows=DATA.wat.map(r=>({...r,_d:p(r.d)}));
const x=d3.scaleUtc(d3.extent(rows,r=>r._d),[m.l,W-m.r]);
const y=d3.scaleLinear([0,120],[H-m.b,m.t]);
const s=d3.select('#w');
[0,40,80,120].forEach(t=>{s.append('line').attr('x1',m.l).attr('x2',W-m.r).attr('y1',y(t)).attr('y2',y(t)).attr('stroke','var(--grid)').attr('stroke-width',2);
 s.append('text').attr('class','tick num').attr('x',m.l-14).attr('y',y(t)).attr('dy','0.35em').attr('text-anchor','end').text(t);});
s.append('line').attr('x1',m.l).attr('x2',W-m.r).attr('y1',y(0)).attr('y2',y(0)).attr('stroke','var(--axis)').attr('stroke-width',2);
['2026-09-01','2026-09-15','2026-10-01','2026-10-15','2026-11-01'].forEach(d=>s.append('text').attr('class','tick').attr('x',x(p(d))).attr('y',H-8).attr('text-anchor','middle').text(d3.utcFormat('%b %-d')(p(d))));
const g=s.append('defs').append('linearGradient').attr('id','fw').attr('x1',0).attr('x2',0).attr('y1',0).attr('y2',1);
[[0,.26],[.5,.08],[1,0]].forEach(([o,a])=>g.append('stop').attr('offset',o).attr('stop-color','#00849C').attr('stop-opacity',a));
s.append('path').datum(rows).attr('fill','url(#fw)').attr('d',d3.area().x(r=>x(r._d)).y0(y(0)).y1(r=>y(r.w)));
s.append('path').datum(rows).attr('fill','none').attr('stroke','var(--teal)').attr('stroke-width',5).attr('stroke-linejoin','round').attr('d',d3.line().x(r=>x(r._d)).y(r=>y(r.w)));
const L=rows[rows.length-1];
s.append('circle').attr('cx',x(L._d)).attr('cy',y(L.w)).attr('r',9).attr('fill','var(--teal)').attr('stroke','var(--paper)').attr('stroke-width',4);
s.append('text').attr('class','num').attr('x',x(L._d)-18).attr('y',y(L.w)-22).attr('text-anchor','end').attr('font-size',30).attr('font-weight',600).attr('fill','var(--ink)').text(L.w+' on 4 Nov');
const F0=rows.find(r=>r.w>0); s.append('text').attr('class','num').attr('x',x(F0._d)-14).attr('y',y(F0.w)-16).attr('text-anchor','end').attr('font-size',24).attr('fill','var(--muted)').text('first one, 10 Sep');
""")

# ---------------------------------------------------------------- slide 5
SRC = ["ChatGPT", "Direct or shared link", "Google", "Bing", "Site's own pages"]
COL = ["var(--teal)", "var(--amber)", "var(--purple)", "var(--rust)", "var(--blue)"]
leg = "".join(f'<span><i class="sw" style="background:{c}"></i>{s}</span>' for s, c in zip(SRC, COL))
tot = {}
for r in src:
    tot[r["window_end"]] = tot.get(r["window_end"], 0) + int(r["sessions"])
s5 = page(f"""
<h1>Visits doubled in a week</h1>
<p class="sub">Sessions in each three-day window, by source. Search engines sent nobody until 20 October.</p>
<div class="legend">{leg}</div>
<div class="main"><svg id="t" width="912" height="640"></svg></div>
<p class="src">Microsoft Clarity. Each bar is the three days ending on that date.</p>""", 5, script=f"""
const SRC={json.dumps(SRC)}, COL={json.dumps(COL)};
const W=912,H=640,m={{l:70,r:16,t:60,b:50}};
const wins=[...new Set(DATA.src.map(r=>r.w))].sort();
const p=d3.utcParse('%Y-%m-%d');
const x=d3.scaleBand(wins,[m.l,W-m.r]).paddingInner(0.38).paddingOuter(0.15);
const y=d3.scaleLinear([0,90],[H-m.b,m.t]);
const s=d3.select('#t');
[0,30,60,90].forEach(t=>{{s.append('line').attr('x1',m.l).attr('x2',W-m.r).attr('y1',y(t)).attr('y2',y(t)).attr('stroke','var(--grid)').attr('stroke-width',2);
 s.append('text').attr('class','tick num').attr('x',m.l-14).attr('y',y(t)).attr('dy','0.35em').attr('text-anchor','end').text(t);}});
wins.forEach(w=>{{let acc=0; const bw=x.bandwidth();
 SRC.forEach((src,i)=>{{const n=(DATA.src.find(r=>r.w===w&&r.s===src)||{{n:0}}).n; if(!n)return;
  const y0=y(acc), y1=y(acc+n); s.append('rect').attr('x',x(w)).attr('width',bw).attr('y',y1+(acc?0:0)).attr('height',Math.max(0,y0-y1-3)).attr('rx',4).attr('fill',COL[i]); acc+=n;}});
 s.append('text').attr('class','num').attr('x',x(w)+bw/2).attr('y',y(acc)-16).attr('text-anchor','middle').attr('font-size',32).attr('font-weight',700).attr('fill','var(--ink)').text(acc);
 s.append('text').attr('class','tick').attr('x',x(w)+bw/2).attr('y',H-12).attr('text-anchor','middle').text(d3.utcFormat('%b %-d')(p(w)));}});
s.append('line').attr('x1',m.l).attr('x2',W-m.r).attr('y1',y(0)).attr('y2',y(0)).attr('stroke','var(--axis)').attr('stroke-width',2);
""")

# ---------------------------------------------------------------- slide 6
TL = [("Aug 22", "The first post is collected"),
      ("Aug 29", "Photos of handwritten lists become searchable text"),
      ("Sep 1", "A parser reads long found-vehicle lists without inventing numbers"),
      ("Oct 9", "The first two verified leads reach the families"),
      ("Oct 13", "Reposts merged into single reports; search gets fast"),
      ("Oct 20", "The code is made open source"),
      ("Oct 27", "Every match is checked against its source before anyone is told"),
      ("Oct 28", "Listed on Google and Bing; a Facebook Page joins the recovery groups"),
      ("Nov 4", "The Page is in 18 groups and visits double")]
tl_html = "".join(
    f'<div style="display:grid;grid-template-columns:120px 34px 1fr;align-items:start;gap:0 18px">'
    f'<span class="num" style="font-size:26px;color:var(--muted);padding-top:3px">{d}</span>'
    f'<span style="position:relative;height:100%">{"" if i == len(TL)-1 else '<i style="position:absolute;left:15px;top:0;bottom:-30px;width:4px;background:var(--grid)"></i>'}'
    f'<i style="position:absolute;left:5px;top:6px;width:24px;height:24px;border-radius:50%;background:{"var(--teal)" if i < len(TL)-1 else "var(--amber)"};border:4px solid var(--paper)"></i></span>'
    f'<span style="font-size:29px;line-height:1.3">{t}</span></div>'
    for i, (d, t) in enumerate(TL))
s6 = page(f"""
<h1>75 days</h1>
<p class="sub">What changed, in order.</p>
<div class="main" style="gap:30px;justify-content:flex-start;margin-top:44px">{tl_html}</div>""", 6)

# ---------------------------------------------------------------- slide 7
s7 = page("""
<div class="main" style="justify-content:center">
  <p style="font-size:84px;line-height:1.08;font-weight:600;margin:0;letter-spacing:-1px">Our own car is still not in it.</p>
  <p style="font-size:84px;line-height:1.08;font-weight:600;margin:28px 0 0;color:var(--gold);letter-spacing:-1px">Someone else's might be.</p>
  <p style="font-size:34px;line-height:1.45;margin:80px 0 0;color:var(--mist);max-width:860px">Search free by chassis or plate number at <b style="color:#fff">mycarsd.online</b>. No fees, no brokers. Official recovery goes through sudanpolice.net and the Traffic Department.</p>
  <p style="font-size:26px;margin:40px 0 0;color:var(--mist)">Open source: github.com/FirasFirari/arabati-wein</p>
</div>""", 7, dark=True)

SLIDES = [s1, s2, s3, s4, s5, s6, s7]
with sync_playwright() as pw:
    b = pw.chromium.launch()
    files = []
    for i, html in enumerate(SLIDES, 1):
        pg = b.new_page(device_scale_factor=1, viewport={"width": W, "height": H})
        errs = []
        pg.on("pageerror", lambda e: errs.append(str(e)))
        pg.set_content(html, wait_until="networkidle")
        pg.evaluate("document.fonts.ready")
        pg.wait_for_timeout(700)
        over = pg.evaluate("document.documentElement.scrollHeight > 1350 || document.body.scrollHeight > 1350")
        f = OUT / f"slide-{i}.png"
        pg.screenshot(path=str(f), clip={"x": 0, "y": 0, "width": W, "height": H})
        files.append(f)
        print(f.name, "overflow" if over else "ok", f.stat().st_size, errs[:2])
        pg.close()
    # the PDF carousel: one 1080x1350 page per slide, printed by the browser
    imgs = "".join(
        f'<div class="pg"><img src="data:image/png;base64,{base64.b64encode(f.read_bytes()).decode()}"></div>' for f in files)
    pg = b.new_page()
    pg.set_content(f"""<!doctype html><html><head><style>@page{{size:1080px 1350px;margin:0}}
html,body{{margin:0}}.pg{{width:1080px;height:1350px;page-break-after:always;overflow:hidden}}
.pg:last-child{{page-break-after:auto}}img{{display:block;width:1080px;height:1350px}}</style></head>
<body>{imgs}</body></html>""", wait_until="load")
    pdf = OUT / "arabati-wein-75-days.pdf"
    pg.pdf(path=str(pdf), width="1080px", height="1350px", print_background=True,
           margin={"top": "0", "right": "0", "bottom": "0", "left": "0"})
    print(pdf.name, pdf.stat().st_size)
    b.close()
