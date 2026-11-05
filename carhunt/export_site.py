"""Generate the shareable searchable-database page (site/arabati-wein.html)
from the vehicles table. Re-run after every extraction to refresh the data,
then republish the artifact.

Usage:
    python -m carhunt.export_site
    python -m carhunt.export_site --ui-only   # re-fill the template around the
                                              # CURRENT payload: no DB pass, no RAM,
                                              # core.bin/detail.bin stay byte-identical
"""
import datetime
import gzip
import hashlib
import json
import re

import sys

from . import db
from . import publish_guard
from .aliases import ALIASES
from .config import PROJECT_ROOT, load_config

OUT = PROJECT_ROOT / "site" / "arabati-wein.html"

# The Arabic official-channels block, used twice: static HTML in #official-help
# (for crawlers) and the JS helpHtml() literal (for the language switch).
HELP_AR = r"""<div class="help">
  <strong>الطرق الرسمية المجانية — تعمل حتى لو لم تجد عربتك هنا:</strong>
  <ol>
    <li><strong>بلّغ إلكترونياً مجاناً:</strong>
      <a href="https://sudanpolice.net/reg/car_stealing" target="_blank" rel="noopener">sudanpolice.net/reg/car_stealing</a>
      — البلاغ يضع <strong>حظراً قانونياً</strong> على ترخيص العربة ويدرجها في
      منصة الإنتربول، فلا يستطيع أحد إعادة تسجيلها.</li>
    <li><strong>اذهب بنفسك:</strong> إدارة المرور أو فرعية مكافحة سرقة السيارات
      (المباحث) — اسأل هناك عن أحواش الحجز والمركبات المجمَّعة.
      <strong>لا يوجد رقم واتساب رسمي</strong>، وأي حساب يقدّم نفسه على أنه كذلك
      ليس جهة رسمية.</li>
    <li><strong>الأحواش الرسمية تُفحص بالشاسي عند البوابة:</strong>
      حوش المرور — شارع الأربعين، أم درمان · حوش المرور — الخرطوم بحري ·
      أحواش لجنة سحب وتجميع المركبات بولاية الخرطوم.
      خذ إثبات هوية ومستندات الملكية.</li>
    <li>السجل يتحدّث باستمرار — <strong>عد وابحث مرة أخرى لاحقاً.</strong></li>
  </ol>
  <div class="danger-box"><strong>احذر النصب:</strong> الجهات الرسمية لا تعمل عبر
  واتساب شخصي ولا تطلب رسوماً لـ«الإفراج» عن عربتك. لا ترسل رقم الشاسي في رسائل
  خاصة لأي «منصة بحث»، ولا تدفع مالاً مقدماً، ولا تُملِ رمز التحقق المكوَّن من
  ٦ أرقام لأي شخص. الاسترداد الرسمي مجاني.</div>
</div>"""

# The page itself -- HTML, CSS and the search JavaScript -- lives in
# site/src/index.template.html (moved out of this file 2026-11-05, byte for
# byte; tests/test_export_snapshot.py proves the built page is unchanged).
# fill_template() replaces its __TOKENS__. The JS normalisation in it must
# stay equivalent to carhunt/normalize.py: tests/test_js_parity.py and
# tests/test_search_parity.py run it in node to check.
TEMPLATE_PATH = PROJECT_ROOT / "site" / "src" / "index.template.html"
TEMPLATE = TEMPLATE_PATH.read_text(encoding="utf-8")


# --- personal data guard ---------------------------------------------------
# Vision agents transcribe photographed Sudanese vehicle-registration cards
# (رخصة مركبة / استمارة), which carry the registered owner's NAME, HOME ADDRESS
# and personal phone. A poster who writes "call me on 09xx" has chosen to be
# contactable; the owner of a registration card photographed and posted by a
# stranger has not. Republishing that on an indexed public page - during a war,
# tying a named person to an address and a vehicle - is a different act from
# hosting a sighting, so the descriptions are neutralised on the way out.
#
# The DB keeps the full text: the family still needs it locally to identify
# their own car. Only the published payload is redacted.
_DOC_MARKERS = (
    "registration card", "registration document", "vehicle-registration",
    "رخصة مركبة", "استمارة", "owner name", "owner named",
    # document FIELD labels read off photographed cards (2026-11-04: 40 photo
    # rows carried an ID-number field, ~20 a name field)
    "الاسم:", "اسم:", "اسم المالك", "رقم الهوية", "الرقم الوطني", "رقم الجواز",
    "owner:", "address:", "national id",
)
_REDACTED = "قُرئ من صورة وثيقة تسجيل مركبة"   # "read from a vehicle registration document photo"


def redact_personal(description):
    """Drop owner names/addresses read off official documents."""
    if not description:
        return description
    low = str(description).lower()
    if any(m in low for m in _DOC_MARKERS):
        return _REDACTED
    return description


def js_literal(obj) -> str:
    """JSON safe to embed inside a <script> element.

    json.dumps leaves '<' untouched, so a scraped post containing
    "</script>" would close the tag and inject markup into the published
    page. Escaping the three characters that can break out of a script
    context keeps the payload inert while staying valid JSON.
    """
    return (json.dumps(obj, ensure_ascii=False)
            .replace("<", "\\u003c")
            .replace(">", "\\u003e")
            .replace("&", "\\u0026"))


def source_names() -> dict:
    """Facebook group id -> readable name, from config.yaml.

    The registry stores sources as `facebook:<numeric id>`, which is what the
    page used to print. Member counts and notes in the configured name
    ("... (128K)", "... — was listed as ...") are trimmed off.
    """
    out = {}
    cfg = load_config()
    for g in ((cfg.get("facebook") or {}).get("groups") or []):
        gid = str(g.get("id") or "").strip()
        name = " ".join(str(g.get("name") or "").split())
        name = name.split(" — ")[0]
        name = re.sub(r"\s*\([^()]*\)\s*$", "", name).strip()
        if gid and name and not name.startswith("("):
            out[gid] = name
    return out


def clarity_snippet() -> str:
    cid_file = OUT.parent / "clarity_id.txt"
    if not cid_file.exists():
        return ""
    cid = cid_file.read_text(encoding="utf-8").strip()
    if not cid:
        return ""
    # hostname-gated: the artifact copy and localhost stay untracked
    return (
        '<script>\n'
        'if (/(^|\\.)mycarsd\\.(online|netlify\\.app)$/.test(location.hostname)) {\n'
        '  (function(c,l,a,r,i,t,y){c[a]=c[a]||function(){(c[a].q=c[a].q||[]).push(arguments)};\n'
        '   t=l.createElement(r);t.async=1;t.src="https://www.clarity.ms/tag/"+i;\n'
        '   y=l.getElementsByTagName(r)[0];y.parentNode.insertBefore(t,y);\n'
        '  })(window, document, "clarity", "script", "' + cid + '");\n'
        '}\n'
        '</script>'
    )


def data_host() -> str:
    """Base URL the page loads the payloads from first (config
    `cloudflare.data_host`, e.g. https://mycarsd-data.pages.dev/), or ''."""
    try:
        host = str(((load_config() or {}).get("cloudflare") or {}).get("data_host") or "").strip()
    except Exception:
        host = ""
    if host and not host.endswith("/"):
        host += "/"
    return host if re.fullmatch(r"https://[A-Za-z0-9.\-]+/", host) else ""


def fill_template(*, stats_js: str, fbkind_js: str, core_url: str,
                  detail_url: str, updated: str) -> str:
    return (TEMPLATE
            .replace("__DATA_HOST__", data_host())
            .replace("__CLARITY__", clarity_snippet())
            .replace("__ALIASES__", js_literal(ALIASES))
            .replace("__STATS__", stats_js)
            .replace("__FBKIND__", fbkind_js)
            .replace("__SRCNAMES__", js_literal(source_names()))
            .replace("__CORE_URL__", core_url)
            .replace("__DETAIL_URL__", detail_url)
            .replace("__HELP_AR__", HELP_AR)
            .replace("__UPDATED__", updated))


def rebuild_ui() -> None:
    """Rebuild the page shell around the payload that is ALREADY built.

    Reads the embedded constants back out of site/arabati-wein.html and
    re-fills TEMPLATE with them, so a markup/script/CSS change ships without
    the 3-minute, multi-GB database pass. core.bin and detail.bin are not
    touched and keep their ?v= hashes, which build_deploy.py checks.

    Every pattern is single-line on purpose: with re.S the lazy `const X =`
    match once swallowed half the file (143K chars instead of 70K).
    """
    if not OUT.exists():
        sys.exit(f"{OUT} does not exist — run a full export first")
    html = OUT.read_text(encoding="utf-8")

    def grab(pattern: str) -> str:
        m = re.search(pattern, html, re.M)
        if not m:
            sys.exit(f"--ui-only: cannot find {pattern!r} in {OUT.name}; run a full export")
        return m.group(1)

    out = fill_template(
        stats_js=grab(r"^const STATS = (.*);$"),
        fbkind_js=grab(r"^const FBKIND = (\{.*\});   // facebook"),
        core_url=grab(r"^const CORE_URL = '(core\.bin\?v=[0-9a-f]{12})';$"),
        detail_url=grab(r"^const DETAIL_URL = '(detail\.bin\?v=[0-9a-f]{12})';$"),
        updated=grab(r'<span id="upd">(\d{4}-\d{2}-\d{2})</span>'),
    )
    for name in ("core.bin", "detail.bin"):
        blob = OUT.parent / name
        if not blob.exists():
            sys.exit(f"{blob} is missing — the page would point at nothing")
        want = hashlib.sha1(blob.read_bytes()).hexdigest()[:12]
        if f"{name}?v={want}" not in out:
            sys.exit(f"{name} on disk (v={want}) is not the build the page embeds; run a full export")
    leftover = re.findall(r"__[A-Z_]+__", out)
    if leftover:
        sys.exit(f"--ui-only: unfilled tokens {sorted(set(leftover))}")
    OUT.write_text(out, encoding="utf-8")
    print(f"Rebuilt {OUT} ({OUT.stat().st_size/1024:.0f} KB shell) around the existing "
          f"core.bin/detail.bin — no data change")


def main() -> None:
    db.init()
    vehicles = db.all_vehicles()
    def trim(value, limit):
        if value is None:
            return None
        text = str(value).strip()
        if not text:
            return None
        return text[:limit]



    # Numbers the owner has ruled must never be published (config.yaml
    # publish.forbidden_contacts). A contact holding one is dropped; inside
    # free text it is masked. publish_guard re-checks the packed payload below.
    forbidden = publish_guard.forbidden_patterns()

    # keep the payload lean: tens of thousands of records ship to the browser
    records = []
    for v in vehicles:
        rec = {
            "make": trim(v.get("make"), 30),
            "model": trim(v.get("model"), 30),
            "color": trim(v.get("color"), 25),
            "year": trim(v.get("year"), 12),
            "chassis": trim(v.get("chassis"), 30),
            "plate": trim(v.get("plate"), 30),
            "status": trim(v.get("status"), 20),
            "location": trim(v.get("location"), 30),
            "contact": trim(v.get("contact"), 40),
            "description": trim(redact_personal(v.get("description")), 160),
            "url": trim(v.get("url"), 120),
            "source": trim(v.get("source"), 40),
            "seen_at": (v.get("seen_at") or "")[:10] or None,
            "model_key": trim(v.get("model_key"), 30),
        }
        if forbidden:
            if publish_guard.contains(rec.get("contact"), forbidden):
                rec["contact"] = None
            for field in ("description", "location"):
                rec[field] = publish_guard.mask(rec.get(field), forbidden)
        # A number in free text that is not the row's own (offered) contact
        # belongs to someone who never chose to be listed: masked.
        for field in ("description", "location"):
            rec[field] = publish_guard.mask_foreign_phones(rec.get(field), rec.get("contact"))
        rec = {k: val for k, val in rec.items() if val is not None}
        if v.get("from_photo"):
            # read by a vision model from a photo — less reliable than a typed
            # list, so the page warns readers to check the original post
            rec["ph"] = 1
        if v.get("low_confidence"):
            # STRONGER than `ph`. `ph` says where the number came from; this
            # says the CHARACTERS THEMSELVES are disputed — two independent
            # readers disagreed and the stamping is too worn to settle it.
            # Shown, never hidden: it could still be somebody's car. But shown
            # with the doubt attached, so nobody is told a match on the
            # strength of four ambiguous characters.
            rec["lc"] = 1
        records.append(rec)

    # ---- normalisation mirrors of the browser helpers --------------------
    # These must match the JS in the template exactly, or a number typed on the
    # page will not find the index entry built here.
    _AR_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
    _AR_LETTERS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ة": "ه",
                                 "ى": "ي", "ؤ": "و", "ئ": "ي"})
    _AR_MARKS = re.compile("[\u0640\u064B-\u065F\u0670]")   # not U+0660-9: those are digits
    _VIN_FOLD = str.maketrans("OIQ", "010")      # mirrors normalize.norm_chassis

    def tr_ar(value):
        return str(value or "").translate(_AR_DIGITS)

    def digits_only(value):
        return re.sub(r"\D", "", tr_ar(value))

    def norm_chassis(value):
        v = re.sub(r"[^A-Z0-9]", "", tr_ar(value).upper())
        return v.translate(_VIN_FOLD) if len(v) == 17 else v

    def norm_ar(value):
        return _AR_MARKS.sub("", str(value or "").lower().translate(_AR_LETTERS))

    def dossier_key(rec):
        chassis = norm_chassis(rec.get("chassis"))
        if len(chassis) >= 4:
            return "c:" + chassis
        plate = re.sub(r"[^0-9\u0621-\u064A]", "", tr_ar(rec.get("plate")))
        if len(digits_only(plate)) >= 3:
            return "p:" + plate
        return None

    def mode_of(values):
        counts, best, best_n = {}, None, 0
        for raw in values:
            text = str(raw or "").strip()
            if not text:
                continue
            n = counts.get(text, 0) + 1
            counts[text] = n
            if n > best_n:
                best_n, best = n, text
        return best

    def longest_of(values):
        best = ""
        for raw in values:
            text = str(raw or "")
            if len(text) > len(best):
                best = text
        return best

    # ---- group into dossiers ONCE, here, instead of on every phone -------
    # A dossier is one physical car: the same recovery list gets reposted
    # between groups, sellers relist, police inventories overlap. Grouping is
    # by EXACT normalised identifier only — suffix-merging could fuse two
    # different cars sharing a 6-digit tail, and a wrong merge is worse than
    # showing both, since suffix-tolerant SEARCH surfaces them anyway.
    groups, order_seen = {}, []
    for rec in records:
        key = dossier_key(rec)
        if key is None:
            key = "_loose:%d" % len(order_seen)
        if key not in groups:
            groups[key] = []
            order_seen.append(key)
        groups[key].append(rec)

    # ---- one row per REPORT, not per repost ---------------------------------
    # A recovery list is cross-posted to many groups, so the same line (same
    # description, contact, status and number) arrives again and again.
    # Measured 2026-10-13: the 992,973 sightings held only 393,597 distinct
    # lines per dossier. Shipping every copy cost 60% of the rows, inflated
    # "N sightings" (one list posted 30 times read as 30 sightings), and hid
    # links: Facebook often saves a repost without its permalink while another
    # copy has one, and 298,244 linkless rows had a linked twin. Copies collapse
    # into one row that keeps a linked copy (and its source), the newest date,
    # the copy count `n` and the first date `f`. The photo/doubt flags are part
    # of the key, so a typed line never absorbs a machine-read one.
    copy_key = ("description", "contact", "status", "chassis", "plate",
                "location", "ph", "lc")

    def merge_copies(sightings):          # newest first in, newest first out
        merged, at = [], {}
        for r in sightings:
            k = tuple(r.get(f) for f in copy_key)
            if k not in at:
                at[k] = len(merged)
                merged.append({"row": r, "n": 1, "first": r.get("seen_at"),
                               "linked": r if r.get("url") else None})
                continue
            m = merged[at[k]]
            m["n"] += 1
            seen = r.get("seen_at")
            if seen and (not m["first"] or seen < m["first"]):
                m["first"] = seen
            if m["linked"] is None and r.get("url"):
                m["linked"] = r
        out = []
        for m in merged:
            row = dict(m["row"])
            if m["linked"] is not None and m["linked"] is not m["row"]:
                row["url"] = m["linked"]["url"]
                row["source"] = m["linked"].get("source")
            if m["n"] > 1:
                row["n"] = m["n"]
                if m["first"] and m["first"] != row.get("seen_at"):
                    row["f"] = m["first"]
            out.append(row)
        return out

    dossiers = []
    for key in order_seen:
        every_copy = sorted(groups[key], key=lambda r: r.get("seen_at") or "", reverse=True)
        sightings = merge_copies(every_copy)
        makes = {str(r.get("make") or "").strip() for r in sightings}
        makes.discard("")
        plate_runs = set()
        for r in sightings:
            plate_text = tr_ar(r.get("plate"))
            for run in re.findall(r"[0-9]{3,}", plate_text):
                plate_runs.add(run)
            # a glued region marker ("خ١١٢٣٤٥") hides the serial the owner
            # remembers, so offer the peeled serial as a candidate too
            glued = re.search(r"[\u0621-\u064A]\s*[0-9]([0-9]{5,})", plate_text)
            if glued:
                plate_runs.add(glued.group(1))
        chassis = longest_of(r.get("chassis") for r in sightings)
        plate = longest_of(r.get("plate") for r in sightings)
        year = mode_of(r.get("year") for r in sightings)
        parts = [mode_of(r.get("make") for r in sightings),
                 mode_of(r.get("model") for r in sightings),
                 mode_of(r.get("color") for r in sightings), year]
        for r in sightings[:3]:
            parts += [r.get("status"), r.get("location"),
                      str(r.get("description") or "")[:80]]
        year_match = re.search(r"(19|20)\d{2}", str(year or ""))
        flags = 0
        if len(makes) > 1:
            # the same number under different makes is the chassis-transplant
            # tell; shown, never hidden — for the owner that IS the information
            flags |= 1
        if any(r.get("ph") for r in sightings):
            flags |= 2
        if any(r.get("lc") for r in sightings):
            flags |= 4
        dossiers.append({
            "sightings": sightings,
            "header": [
                mode_of(r.get("color") for r in sightings) or "",
                parts[0] or "", parts[1] or "", year or "",
                chassis, plate, flags,
                sum(1 for r in sightings if r.get("contact")),
                len(sightings),          # [8] report count: cards + "most" sort before detail.bin lands
            ],
            "cn": key[2:] if key.startswith("c:") else "",
            "pr": ("," + ",".join(sorted(plate_runs)) + ",") if plate_runs else "",
            "hay": norm_ar(" ".join(x for x in parts if x)),
            "yr": int(year_match.group(0)) if year_match else 0,
            "mk": mode_of(r.get("model_key") for r in sightings) or "",
            "lat": (every_copy[0].get("seen_at") or ""),
            "old": (every_copy[-1].get("seen_at") or ""),
        })

    # Pack for transfer. Sighting rows ship as positional arrays, and any field
    # whose values repeat heavily ships as an index into a shared table.
    # Measured on 49,755 rows: seen_at held TWO distinct values yet cost 1.6MB,
    # source 18 values cost 1.0MB, and url only 2,082 distinct cost 1.8MB — one
    # كشف post lists dozens of vehicles, so its link repeats dozens of times.
    # `n` and `f` (copy count, first date) come last so the many single-copy
    # rows lose them to the trailing-zero trim below.
    field_order = ["make", "model", "color", "year", "chassis", "plate",
                   "status", "location", "contact", "description", "url",
                   "source", "seen_at", "model_key", "ph", "lc", "n", "f"]
    shipped = [r for d in dossiers for r in d["sightings"]]
    tables, index = {}, {}
    for field in field_order:
        present = [r[field] for r in shipped if r.get(field) is not None]
        if not present:
            continue
        distinct = sorted({str(v) for v in present})
        if len(distinct) < len(present) * 0.5:   # repetition worth encoding
            tables[field] = [None] + distinct    # 0 therefore means "absent"
            index[field] = {v: i for i, v in enumerate(distinct, start=1)}

    def pack_row(rec):
        row = []
        for field in field_order:
            value = rec.get(field)
            if value is None:
                row.append(0)
            elif field in index:
                row.append(index[field][str(value)])
            else:
                row.append(value)
        while row and row[-1] == 0:              # trailing gaps cost bytes too
            row.pop()
        return row

    def tally(field):
        counts = {}
        for rec in records:
            raw = rec.get(field)
            if raw is None:
                continue
            key = str(raw).strip()
            if not key or len(key) > 40:
                continue
            counts[key] = counts.get(key, 0) + 1
        return sorted(counts.items(), key=lambda kv: -kv[1])

    # ---- two payloads: answer a number first, fill in the details after ----
    # One 9.8MB file that unpacked to 58MB of JSON (90MB before copies were
    # merged) had to finish downloading AND parsing before the page could
    # answer anything; on Sudanese mobile data that is a minute or more, and
    # Clarity (2026-10-13) recorded people leaving before it arrived. Most
    # visitors type a chassis or plate number, and answering THAT needs only
    # the card headers plus the number index, ~1.7MB gzipped. So:
    #   core.bin   - headers, number index, dates, browse order, tallies.
    #                Number search, browsing, sorting and the empty-result
    #                watch all work the moment it lands.
    #   detail.bin - every sighting row, its lookup tables, and the text/model/
    #                year index. Fetched right after core; cards fill in their
    #                timelines, and text search and the dropdown filters wait
    #                for it with a visible progress line.
    core = {
        "k": field_order,
        "hd": [d["header"] for d in dossiers],
        "ix": {
            "cn": [d["cn"] for d in dossiers],
            "pr": [d["pr"] for d in dossiers],
            "lat": [d["lat"] for d in dossiers],
            "old": [d["old"] for d in dossiers],
        },
        # default browse order ships precomputed so the page never sorts 83k
        # entries just to show the newest thirty
        "ord": sorted(range(len(dossiers)), key=lambda i: dossiers[i]["lat"], reverse=True),
        # vehicles a visitor can actually search for by number; the rest are
        # sightings with no identifier at all, hidden behind the toggle
        "nid": sum(1 for d in dossiers if d["cn"] or d["pr"]),
        "tal": {f: tally(f) for f in ("model_key", "color", "status", "location")},
    }
    detail = {
        "t": tables,
        "sg": [[pack_row(r) for r in d["sightings"]] for d in dossiers],
        "ix": {
            "hay": [d["hay"] for d in dossiers],
            "yr": [d["yr"] for d in dossiers],
            "mk": [d["mk"] for d in dossiers],
        },
    }

    stats = db.registry_stats()

    # Facebook source -> "g" (group) or "p" (profile/page), read off the links
    # that DID get saved. After merging copies ~45% of rows still have no link
    # (old Facebook posts, see memory facebook-links); the page offers those a
    # search for the row's number inside the right group or profile instead.
    fb_votes = {}
    con = db.connect()
    try:
        for src, url in con.execute(
                "SELECT source, url FROM posts WHERE source LIKE 'facebook:%' AND url IS NOT NULL"):
            gid = src.split(":", 1)[1]
            g, p = fb_votes.get(gid, (0, 0))
            fb_votes[gid] = (g + 1, p) if "/groups/" in url else (g, p + 1)
    finally:
        con.close()
    fb_kind = {gid: ("g" if g >= p else "p") for gid, (g, p) in fb_votes.items()}

    # The payloads live beside the page, not inside it, so the shell paints
    # before the registry has downloaded. Gzipped here rather than left to the
    # server: that guarantees the wire size regardless of host config, and
    # DecompressionStream unpacks it natively instead of on the main thread.
    #
    # Built BEFORE the HTML because the page has to embed each blob's hash.
    def pack(obj):
        return gzip.compress(json.dumps(obj, ensure_ascii=False).encode("utf-8"), 9)
    core_blob, detail_blob = pack(core), pack(detail)

    # CACHE BUSTING — this is load-bearing, do not drop the ?v=.
    # The page fetches the payloads with `cache: 'force-cache'`, which serves a
    # cached copy WITHOUT revalidating, so a returning visitor kept the payload
    # from their first visit forever: measured 2026-09-01 against the live site,
    # force-cache returned 90,780 dossiers while the deployed file held 91,000.
    # Every data refresh since the payload moved out of the HTML — the Aug 30
    # rebuild included — was invisible to anyone who had visited before.
    # A content hash in the URL makes each build a distinct resource, so
    # force-cache keeps its speed for repeat loads of the SAME data and a new
    # build is fetched fresh.
    core_url = f"core.bin?v={hashlib.sha1(core_blob).hexdigest()[:12]}"
    detail_url = f"detail.bin?v={hashlib.sha1(detail_blob).hexdigest()[:12]}"

    html = fill_template(
        stats_js=js_literal(stats), fbkind_js=js_literal(fb_kind),
        core_url=core_url, detail_url=detail_url,
        updated=datetime.date.today().isoformat())
    # Refuse to write a build that would publish what must not be published.
    # Checked on the bytes that ship, decompressed -- grep over the source
    # cannot see inside a gzip (that is how a ruled-out number stayed live).
    findings = (publish_guard.scan_payload("core.bin", core_blob, forbidden)
                + publish_guard.scan_payload("detail.bin", detail_blob, forbidden)
                + publish_guard.scan_html(OUT.name, html, forbidden))
    if findings:
        sys.exit("publish_guard REFUSED the build, nothing written:\n  " + "\n  ".join(findings))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(html, encoding="utf-8")
    (OUT.parent / "core.bin").write_bytes(core_blob)
    (OUT.parent / "detail.bin").write_bytes(detail_blob)
    stale = OUT.parent / "data.bin"          # the single-file payload, pre-2026-10-13
    if stale.exists():
        stale.unlink()

    stamp = datetime.date.today().isoformat()
    print(f"Wrote {OUT} ({OUT.stat().st_size/1024:.0f} KB shell) + core.bin "
          f"({len(core_blob)/1e6:.1f} MB) + detail.bin ({len(detail_blob)/1e6:.1f} MB) — "
          f"{len(records)} sightings, {len(dossiers)} vehicles, {stamp}")
    linkless_before = sum(1 for r in records if not r.get("url"))
    linkless_after = sum(1 for r in shipped if not r.get("url"))
    print(f"copies merged: {len(records):,} sightings -> {len(shipped):,} rows; "
          f"linkless {linkless_before:,} ({linkless_before/max(1, len(records)):.0%}) -> "
          f"{linkless_after:,} ({linkless_after/max(1, len(shipped)):.0%})")


if __name__ == "__main__":
    if "--ui-only" in sys.argv[1:]:
        rebuild_ui()
    else:
        main()
