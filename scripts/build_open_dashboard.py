"""Render open-data/dashboard.html from the CSVs in open-data/.

One self-contained page (no data requests, no script libraries): a state map,
the location-mapping quality that says how far to trust it, and status, make
and collection charts. It reads only the published CSVs, so it can show
nothing the dataset does not already contain.

    python scripts\\build_open_dataset.py      # first: refresh the CSVs
    python scripts\\build_open_dashboard.py    # then: write open-data/dashboard.html
"""
import csv
import html
import json
import math
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from carhunt.config import PROJECT_ROOT

OD = PROJECT_ROOT / "open-data"
BOUNDS = OD / "map" / "sdn_admin1_simplified.geojson"

# class floor -> CSS class; a state's fill says which order of magnitude it is in
CLASSES = [(5000, "c4", "5,000 or more"), (500, "c3", "500 to 4,999"),
           (50, "c2", "50 to 499"), (5, "c1", "5 to 49")]


def table(name: str) -> list[dict]:
    with open(OD / name, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))
    return rows[1:]                      # drop the HXL hashtag row


def num(row: dict, col: str = "vehicles") -> int | None:
    return int(row[col]) if row[col] != "" else None


def fmt(n) -> str:
    return f"{n:,}"


def esc(s) -> str:
    return html.escape(str(s), quote=True)


def state_map(states: list[dict]) -> str:
    feats = json.loads(BOUNDS.read_text(encoding="utf-8"))["features"]
    by_code = {s["adm1_pcode"]: s for s in states}
    pts = [p for f in feats for poly in f["geometry"]["coordinates"] for p in poly[0]]
    x0, x1 = min(p[0] for p in pts), max(p[0] for p in pts)
    y0, y1 = min(p[1] for p in pts), max(p[1] for p in pts)
    k = math.cos(math.radians((y0 + y1) / 2))        # equirectangular at Sudan's mid-latitude
    W = 560
    s = W / ((x1 - x0) * k)
    H = round((y1 - y0) * s)
    px = lambda x, y: (round((x - x0) * k * s, 1), round((y1 - y) * s, 1))
    shapes, labels = [], []
    for f in feats:
        code = f["properties"]["adm1_pcode"]
        row = by_code[code]
        n = num(row)
        if n is None:
            cls, value = "held", "1 to 4 (number withheld)"
        elif n == 0:
            cls, value = "zero", "none reported in these sources"
        else:
            cls = next(c for floor, c, _ in CLASSES if n >= floor)
            value = f"{fmt(n)} vehicles"
        d = " ".join("M" + " L".join(f"{a},{b}" for a, b in (px(*p) for p in poly[0])) + " Z"
                     for poly in f["geometry"]["coordinates"])
        tip = f"{row['adm1_name_en']} · {row['adm1_name_ar']} ({code}): {value}"
        shapes.append(f'<path class="st {cls}" d="{d}" data-tip="{esc(tip)}" tabindex="0"></path>')
        if n and n >= 500:
            big = max(f["geometry"]["coordinates"], key=lambda poly: len(poly[0]))[0]
            cx = sum(p[0] for p in big) / len(big)
            cy = sum(p[1] for p in big) / len(big)
            lx, ly = px(cx, cy)
            labels.append(f'<text class="maplabel" x="{lx}" y="{ly}">{esc(row["adm1_name_en"])}</text>')
    legend = "".join(f'<span><i class="sw {c}"></i>{esc(t)}</span>' for _, c, t in reversed(CLASSES))
    legend += ('<span><i class="sw held"></i>1 to 4, withheld</span>'
               '<span><i class="sw zero"></i>None reported</span>')
    return (f'<svg viewBox="-4 -4 {W + 8} {H + 8}" role="img" '
            f'aria-label="Map of Sudan: vehicles reported per state">'
            '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" '
            'patternTransform="rotate(45)"><rect width="6" height="6" class="hatchbg"></rect>'
            '<line x1="0" y1="0" x2="0" y2="6" class="hatchln"></line></pattern></defs>'
            + "".join(shapes) + "".join(labels) + f'</svg><div class="legend">{legend}</div>')


def bars(rows: list[tuple[str, int | None, str]], unit: str = "vehicles") -> str:
    """Horizontal bars: (label, value or None when withheld, tooltip detail)."""
    top = max((v or 0) for _, v, _ in rows) or 1
    out = []
    for label, v, detail in rows:
        shown = "1 to 4" if v is None else fmt(v)
        width = 0 if not v else max(0.6, 100 * v / top)
        tip = f"{label}: {shown} {unit}" + (f". {detail}" if detail else "")
        out.append(f'<div class="bar" data-tip="{esc(tip)}" tabindex="0"><span class="bl">{esc(label)}</span>'
                   f'<span class="bt"><i style="width:{width:.1f}%"></i></span>'
                   f'<span class="bv">{shown}</span></div>')
    return '<div class="bars">' + "".join(out) + "</div>"


def columns(months: dict[str, int]) -> str:
    top = max(months.values())
    cols = "".join(
        f'<div class="col" data-tip="{esc(m)}: {fmt(n)} posts collected" tabindex="0">'
        f'<span class="cv">{fmt(n)}</span><i style="height:{100 * n / top:.1f}%"></i>'
        f'<span class="cl">{esc(m)}</span></div>' for m, n in months.items())
    return f'<div class="cols">{cols}</div>'


def build() -> str:
    summary = {r["indicator"]: r["value"] for r in table("registry_summary.csv")}
    states = table("vehicles_by_admin1.csv")
    quality = {r["mapping_class"]: r for r in table("location_mapping_quality.csv")}
    status = table("vehicles_by_status.csv")
    all_makes = table("vehicles_by_make.csv")
    makes = [r for r in all_makes if r["kind"] == "make"][:12]
    no_make = next(num(r) or 0 for r in all_makes if r["kind"] == "not_stated")
    months: dict[str, int] = {}
    for r in table("posts_by_month_platform.csv"):
        months[r["month_collected"]] = months.get(r["month_collected"], 0) + (num(r, "posts") or 0)
    built = json.loads((OD / "build.json").read_text(encoding="utf-8"))["generated_at"][:10]

    total = int(quality["all_vehicles"]["vehicles"])
    mapped = int(quality["state"]["vehicles"])
    none = int(quality["no_location"]["vehicles"])
    q_rows = [(quality[k]["meaning"][:1].upper() + quality[k]["meaning"][1:], num(quality[k]), "")
              for k in ("no_location", "state", "region_only:Kordofan", "unmapped",
                        "country_only", "multiple")]
    s_rows = [(r["status"].replace("_", " ").capitalize(), num(r), r["meaning"]) for r in status]
    m_rows = [(r["make"], num(r), "") for r in makes]
    state_rows = "".join(
        f'<tr><td>{esc(r["adm1_pcode"])}</td><td>{esc(r["adm1_name_en"])}</td>'
        f'<td lang="ar" dir="rtl">{esc(r["adm1_name_ar"])}</td>'
        f'<td class="n">{fmt(num(r)) if num(r) is not None else "1 to 4"}</td></tr>'
        for r in sorted(states, key=lambda r: -(num(r) or 0)))

    page = TEMPLATE
    for key, value in {
        "__BUILT__": built,
        "__VEHICLES__": fmt(total),
        "__POSTS__": fmt(int(summary["posts_collected"])),
        "__PHOTOS__": fmt(int(summary["photos_collected"])),
        "__MAPPED__": fmt(mapped),
        "__MAPPED_PCT__": f"{100 * mapped / total:.0f}",
        "__NOLOC_PCT__": f"{100 * none / total:.0f}",
        "__FIRST__": summary["first_post_collected"],
        "__LATEST__": summary["latest_post_collected"],
        "__KORDOFAN__": fmt(int(quality["region_only:Kordofan"]["vehicles"])),
        "__NOMAKE_PCT__": f"{100 * no_make / total:.0f}",
        "__MAP__": state_map(states),
        "__QUALITY__": bars(q_rows),
        "__STATUS__": bars(s_rows),
        "__MAKES__": bars(m_rows),
        "__MONTHS__": columns(months),
        "__STATE_ROWS__": state_rows,
    }.items():
        page = page.replace(key, value)
    return page


TEMPLATE = """<title>Sudan Looted Vehicles Data</title>
<meta name="claude:viewport-fit-sides" content="cover">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Space+Grotesk:wght@500;600&display=swap">
<style>
/* layout: one reading column; the map and its trust table sit side by side, everything else stacks */
:root{
  --bg:#f7f8fa; --panel:#ffffff; --ink:#10151c; --ink2:#4b5563; --muted:#7b8494;
  --line:#dfe3ea; --mark:#2a78d6; --track:#eceff4; --warn-bg:#fff6dc; --warn-ink:#5c4400;
  --c1:#86b6ef; --c2:#5598e7; --c3:#256abf; --c4:#0d366b; --zero:#ffffff; --edge:#9aa3b2;
  --display:"Space Grotesk","Segoe UI",system-ui,sans-serif;
  --body:"Inter","Segoe UI",system-ui,sans-serif;
}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){
  --bg:#0e1116; --panel:#171b22; --ink:#eef1f5; --ink2:#bcc3cf; --muted:#8892a0;
  --line:#2a303a; --mark:#3987e5; --track:#232933; --warn-bg:#33290c; --warn-ink:#f3dc9a;
  --c1:#184f95; --c2:#256abf; --c3:#5598e7; --c4:#cde2fb; --zero:#171b22; --edge:#5a6473;
  color-scheme:dark}}
:root[data-theme="dark"]{
  --bg:#0e1116; --panel:#171b22; --ink:#eef1f5; --ink2:#bcc3cf; --muted:#8892a0;
  --line:#2a303a; --mark:#3987e5; --track:#232933; --warn-bg:#33290c; --warn-ink:#f3dc9a;
  --c1:#184f95; --c2:#256abf; --c3:#5598e7; --c4:#cde2fb; --zero:#171b22; --edge:#5a6473;
  color-scheme:dark}
body{background:var(--bg);color:var(--ink);font-family:var(--body);font-size:15px;line-height:1.55}
.wrap{max-width:1040px;margin:0 auto;padding-block:28px 56px;
  padding-left:max(16px,env(safe-area-inset-left,0px));padding-right:max(16px,env(safe-area-inset-right,0px));
  display:flex;flex-direction:column;gap:28px}
h1{font-family:var(--display);font-size:clamp(26px,4vw,36px);line-height:1.15;margin:0;text-wrap:balance;font-weight:600}
h2{font-family:var(--display);font-size:19px;margin:0;font-weight:600;text-wrap:balance}
p{margin:0;max-width:68ch}
.sub{color:var(--ink2)}
.eyebrow{font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
header{display:flex;flex-direction:column;gap:10px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:1px;background:var(--line);
  border:1px solid var(--line);border-radius:10px;overflow:hidden}
.kpi{background:var(--panel);padding:16px 18px;display:flex;flex-direction:column;gap:2px}
.kpi b{font-family:var(--display);font-size:28px;font-weight:600;font-variant-numeric:tabular-nums}
.kpi span{color:var(--ink2);font-size:13px}
.warn{background:var(--warn-bg);color:var(--warn-ink);border-radius:10px;padding:14px 18px}
.warn p{max-width:none}
section{display:flex;flex-direction:column;gap:14px}
.two{display:grid;grid-template-columns:minmax(0,1.15fr) minmax(0,1fr);gap:28px;align-items:start}
.two>div{min-width:0;display:flex;flex-direction:column;gap:14px}
@media (max-width:760px){.two{grid-template-columns:minmax(0,1fr)}}
svg{width:100%;height:auto;display:block}
.st{stroke:var(--bg);stroke-width:1.5;outline:none;cursor:default}
.st:hover,.st:focus-visible{stroke:var(--ink);stroke-width:2}
.st.c1,.sw.c1{fill:var(--c1);background:var(--c1)} .st.c2,.sw.c2{fill:var(--c2);background:var(--c2)}
.st.c3,.sw.c3{fill:var(--c3);background:var(--c3)} .st.c4,.sw.c4{fill:var(--c4);background:var(--c4)}
.st.zero{fill:var(--zero);stroke:var(--edge);stroke-width:1} .st.held{fill:url(#hatch);stroke:var(--edge);stroke-width:1}
.hatchbg{fill:var(--zero)} .hatchln{stroke:var(--c2);stroke-width:2.5}
.maplabel{font:600 12px var(--body);fill:var(--ink);text-anchor:middle;paint-order:stroke;stroke:var(--bg);stroke-width:3.5px;pointer-events:none}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;font-size:13px;color:var(--ink2)}
.legend span{display:inline-flex;align-items:center;gap:6px}
.sw{width:14px;height:14px;border-radius:3px;display:inline-block;border:1px solid var(--edge)}
.sw.zero{background:var(--zero)}
.sw.held{background:repeating-linear-gradient(45deg,var(--c2) 0 2px,var(--zero) 2px 5px)}
.bars{display:flex;flex-direction:column;gap:8px}
.bar{display:grid;grid-template-columns:minmax(0,11.5em) minmax(0,1fr) 5em;gap:10px;align-items:center;outline:none;border-radius:4px}
.bar:focus-visible,.col:focus-visible{box-shadow:0 0 0 2px var(--mark)}
.bl{font-size:13.5px;color:var(--ink2);overflow-wrap:anywhere}
.bt{background:var(--track);height:12px;border-radius:0 4px 4px 0;overflow:hidden}
.bt i{display:block;height:100%;background:var(--mark);border-radius:0 4px 4px 0}
.bv{font-variant-numeric:tabular-nums;text-align:right;font-size:13.5px}
.wide .bar{grid-template-columns:minmax(0,22em) minmax(0,1fr) 5em}
@media (max-width:560px){.wide .bar,.bar{grid-template-columns:minmax(0,1fr) 5em}.bt{grid-column:1/-1;order:3}}
.cols{display:flex;gap:14px;align-items:flex-end;height:180px;border-bottom:1px solid var(--line);padding-bottom:26px;position:relative}
.col{flex:1;max-width:96px;height:100%;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;gap:4px;position:relative;outline:none;border-radius:4px}
.col i{display:block;width:100%;background:var(--mark);border-radius:4px 4px 0 0;min-height:2px}
.cv{font-size:12.5px;font-variant-numeric:tabular-nums;color:var(--ink2)}
.cl{position:absolute;bottom:-22px;font-size:12.5px;color:var(--muted)}
.scroll{overflow-x:auto}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:6px 10px;border-bottom:1px solid var(--line)}
th{font-weight:600;color:var(--ink2);font-size:12px;letter-spacing:.04em;text-transform:uppercase}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
.note{color:var(--ink2);font-size:13.5px}
a{color:var(--mark)}
footer{color:var(--ink2);font-size:13px;border-top:1px solid var(--line);padding-top:16px;display:flex;flex-direction:column;gap:6px}
#tip{position:fixed;z-index:20;pointer-events:none;background:var(--ink);color:var(--bg);padding:7px 10px;border-radius:6px;font-size:13px;max-width:280px;line-height:1.4}
</style>

<div class="wrap">
<header>
  <span class="eyebrow">Arabati Wein · open data · build of __BUILT__</span>
  <h1>Vehicles reported looted or lost in the Sudan war</h1>
  <p class="sub">Counts from the public registry at mycarsd.online, built from recovery lists that committees, volunteers and families post on Facebook and Telegram. No chassis, plate or contact is on this page.</p>
</header>

<div class="kpis">
  <div class="kpi"><b>__VEHICLES__</b><span>distinct vehicles identified by chassis or plate</span></div>
  <div class="kpi"><b>__POSTS__</b><span>public posts collected, __FIRST__ to __LATEST__</span></div>
  <div class="kpi"><b>__PHOTOS__</b><span>photos collected with those posts</span></div>
  <div class="kpi"><b>__MAPPED__</b><span>vehicles whose reported location maps to one state (__MAPPED_PCT__%)</span></div>
</div>

<div class="warn"><p><strong>Read the map as a picture of the sources.</strong> __NOLOC_PCT__% of vehicles have no location in any post. The map shows only the __MAPPED_PCT__% that name a state, it shows where a vehicle was reported and not where it was taken, and a blank state is one these groups did not post about.</p></div>

<section>
  <div class="two">
    <div>
      <h2>Where vehicles are reported, by state</h2>
      __MAP__
    </div>
    <div>
      <h2>How much of the registry the map covers</h2>
      <p class="note">Every vehicle falls in one row or more. A vehicle seen in two posts can be in two rows.</p>
      __QUALITY__
      <p class="note">__KORDOFAN__ vehicles are listed under "Kordofan" with no state. They are left off the map because three states share that name.</p>
    </div>
  </div>
</section>

<section>
  <div class="two">
    <div class="wide">
      <h2>What the posts say happened to the vehicle</h2>
      <p class="note">A vehicle listed as found in one post and for sale in another counts under both.</p>
      __STATUS__
    </div>
    <div>
      <h2>Most common makes</h2>
      <p class="note">__NOMAKE_PCT__% of vehicles are listed with no make.</p>
      __MAKES__
    </div>
  </div>
</section>

<section>
  <h2>Posts collected per month</h2>
  <p class="note">The month a post was collected, not the month a vehicle was taken. Collection began on __FIRST__; the last month is partial.</p>
  __MONTHS__
</section>

<section>
  <h2>State table</h2>
  <div class="scroll"><table>
    <thead><tr><th>P-code</th><th>State</th><th>الولاية</th><th class="n">Vehicles</th></tr></thead>
    <tbody>__STATE_ROWS__</tbody>
  </table></div>
</section>

<footer>
  <span>Data, definitions and caveats: <a href="https://github.com/FirasFirari/arabati-wein/tree/main/open-data">github.com/FirasFirari/arabati-wein/open-data</a> (CC BY 4.0). Search one vehicle: <a href="https://mycarsd.online">mycarsd.online</a>.</span>
  <span>State boundaries and P-codes: OCHA, Sudan Subnational Administrative Boundaries v03 (CC BY-IGO), simplified for display. Boundaries shown do not imply endorsement.</span>
  <span>Counts under 5 are withheld. Contact: info@mycarsd.online</span>
</footer>
</div>
<div id="tip" hidden></div>
<script>
(function(){
  var tip=document.getElementById('tip');
  function show(el,x,y){tip.textContent=el.getAttribute('data-tip');tip.hidden=false;
    var w=tip.offsetWidth,h=tip.offsetHeight;
    tip.style.left=Math.max(8,Math.min(x+14,window.innerWidth-w-8))+'px';
    tip.style.top=(y+h+24>window.innerHeight?y-h-12:y+18)+'px';}
  document.addEventListener('pointermove',function(e){var el=e.target.closest('[data-tip]');
    if(el)show(el,e.clientX,e.clientY);else tip.hidden=true;});
  document.addEventListener('focusin',function(e){var el=e.target.closest('[data-tip]');
    if(el){var r=el.getBoundingClientRect();show(el,r.left+r.width/2,r.top+r.height/2);}});
  document.addEventListener('focusout',function(){tip.hidden=true;});
})();
</script>
"""


def main() -> None:
    out = OD / "dashboard.html"
    out.write_text(build(), encoding="utf-8", newline="\n")
    print(f"written: {out.relative_to(PROJECT_ROOT)} ({out.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
