"""Phone layout check: no page may be wider than a phone screen, in any state.

    python scripts/check_mobile_layout.py                 # serves site/deploy locally
    python scripts/check_mobile_layout.py --base URL      # a draft deploy or the live site
    python scripts/check_mobile_layout.py --shots DIR     # also save a screenshot per state

netlify_deploy.py runs it against the DRAFT before publishing.

Why it exists (2026-11-07). The honeypot field in the watch form was hidden
with `left:-9999px`. In a right-to-left page that space is scrollable, so the
moment a search found nothing -- 95% of searches, the exact moment the form
appears -- the page became 10,359px wide and phones zoomed out or slid
sideways. Nothing failed, no test noticed, and the owner found it on his own
phone. The page looked fine on first load, so only a check that drives the
page into its real states catches this class of bug.

Each public page is loaded at 360px and at 277px (a 360px phone with the
system text size at 130%, which Chrome on Android applies as page zoom), in
light and dark. The home page is also checked after a search that finds a
vehicle, after one that finds nothing (watch form open), with the advanced
filters open, and in English.

Fails when:
  * the document is wider than the screen (horizontal scroll / zoom-out);
  * a visible text field or select has text smaller than 16px (iPhone Safari
    zooms the page in when it is tapped, and leaves it zoomed).

Needs Playwright + Chromium. Without them it prints SKIP and exits 0, unless
--strict is given.
"""
import contextlib
import functools
import http.server
import pathlib
import sys
import threading

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

from carhunt.config import PROJECT_ROOT

DEPLOY = PROJECT_ROOT / "site" / "deploy"
PAGES = ["", "en.html", "guide.html", "scams.html", "yards.html",
         "support.html", "privacy.html", "stats.html"]
VIEWPORTS = [("360", 360), ("277", 277)]   # 277 = 360px at 130% system text
SCHEMES = ["light", "dark"]
UA = ("Mozilla/5.0 (Linux; Android 13; SM-A135F) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/130.0 Mobile Safari/537.36")
# Long digit-only numbers. Digits only, so the page answers from core.bin
# without waiting for detail.bin (a query with letters is a text search and
# waits). Twelve digits match no stored number whole; at most a stored
# fragment, which also opens the watch form ("matches only part"). A second
# candidate covers the unlikely case that the first matches something whole.
MISS_QUERIES = ["804519372661", "390275618404"]

PROBE = r"""
() => {
  const de = document.documentElement;
  const vw = de.clientWidth;
  // The widest offenders, innermost first, for the failure message.
  const bad = el => {
    const r = el.getBoundingClientRect();
    return (r.width || r.height) && (r.right > vw + 1 || r.left < -1);
  };
  const culprits = [];
  if (de.scrollWidth > vw + 1) {
    for (const el of document.querySelectorAll('body *')) {
      if (!bad(el) || [...el.children].some(bad)) continue;
      if (el.closest('details:not([open]) > :not(summary)')) continue;
      const r = el.getBoundingClientRect();
      culprits.push(`${el.tagName.toLowerCase()}${el.id ? '#' + el.id : ''}` +
        `${typeof el.className === 'string' && el.className ? '.' + el.className.trim().split(/\s+/).join('.') : ''}` +
        ` [${Math.round(r.left)}..${Math.round(r.right)}]`);
      if (culprits.length >= 5) break;
    }
  }
  // iPhone Safari zooms in on focus of any field with text under 16px.
  const small = [];
  for (const el of document.querySelectorAll('input, select, textarea')) {
    if (['checkbox', 'radio', 'hidden', 'submit', 'button', 'reset'].includes(el.type)) continue;
    const r = el.getBoundingClientRect();
    if (!r.width || !r.height || el.closest('.vh, .hp, [hidden]')) continue;
    const px = parseFloat(getComputedStyle(el).fontSize);
    if (px < 15.5) small.push(`${el.id || el.name || el.tagName.toLowerCase()} ${px}px`);
  }
  return {vw, sw: de.scrollWidth, culprits, small};
}
"""


@contextlib.contextmanager
def local_server(root: pathlib.Path):
    """Serve `root` on an ephemeral localhost port for the duration."""
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    srv = http.server.ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(Quiet, directory=str(root)))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_address[1]}/"
    finally:
        srv.shutdown()


def run(base: str, shots: pathlib.Path | None) -> int:
    from playwright.sync_api import sync_playwright

    failures = 0

    def judge(label: str, page) -> None:
        nonlocal failures
        r = page.evaluate(PROBE)
        problems = []
        if r["sw"] > r["vw"] + 1:
            problems.append(f"page is {r['sw']}px wide on a {r['vw']}px screen: "
                            + ", ".join(r["culprits"] or ["(no single culprit found)"]))
        if r["small"]:
            problems.append("text under 16px in fields (iPhone zooms on tap): " + ", ".join(r["small"]))
        failures += bool(problems)
        print(f"{'FAIL' if problems else 'ok  '} {label}" + "".join(f"\n       {p}" for p in problems))
        if shots:
            page.screenshot(path=str(shots / (label.replace(' ', '_').replace('/', '-') + ".png")))

    with sync_playwright() as p:
        browser = p.chromium.launch()
        for vname, width in VIEWPORTS:
            for scheme in SCHEMES:
                ctx = browser.new_context(
                    viewport={"width": width, "height": 760}, device_scale_factor=2,
                    is_mobile=True, has_touch=True, user_agent=UA, color_scheme=scheme, locale="ar")
                # Never let a check pollute the real analytics.
                ctx.route("**/*clarity.ms/**", lambda route: route.abort())
                for path in PAGES:
                    page = ctx.new_page()
                    name = path.removesuffix(".html") or "home"
                    tag = f"{name} {vname}px {scheme}"
                    page.goto(base + path, wait_until="load", timeout=120_000)
                    if path:
                        page.wait_for_timeout(500)
                        judge(tag, page)
                        page.close()
                        continue
                    # Home: wait for the registry, then drive every state.
                    page.wait_for_function(
                        "document.querySelectorAll('#results .card').length > 0", timeout=180_000)
                    judge(f"{tag} browse", page)
                    chassis = page.evaluate(
                        "(document.querySelector('#results .idrow bdi') || {}).textContent || ''").strip()
                    if chassis:
                        page.fill("#q", chassis)
                        page.wait_for_timeout(1500)
                        judge(f"{tag} hit", page)
                    for q in MISS_QUERIES:
                        page.fill("#q", q)
                        try:
                            page.wait_for_selector("#watch-box:not([hidden])", timeout=15_000)
                        except Exception:
                            continue
                        page.evaluate("document.getElementById('watch-box').scrollIntoView()")
                        judge(f"{tag} miss+watch form", page)
                        break
                    else:
                        failures += 1
                        print(f"FAIL {tag} miss: the watch form never appeared for "
                              f"{' or '.join(MISS_QUERIES)}")
                    page.fill("#q", "")
                    page.wait_for_timeout(800)
                    page.evaluate("document.querySelector('details.filterwrap').open = true;"
                                  "document.querySelector('details.sources').open = true")
                    page.wait_for_timeout(300)
                    judge(f"{tag} filters open", page)
                    page.click("#lang-toggle")
                    page.wait_for_timeout(800)
                    judge(f"{tag} english", page)
                    page.click("#lang-toggle")       # leave the stored preference as it was
                    page.close()
                ctx.close()
        browser.close()
    return failures


def main() -> None:
    strict = "--strict" in sys.argv
    shots = None
    if "--shots" in sys.argv:
        shots = pathlib.Path(sys.argv[sys.argv.index("--shots") + 1])
        shots.mkdir(parents=True, exist_ok=True)
    try:
        import playwright  # noqa: F401
    except ImportError:
        print("SKIP phone layout check: Playwright is not installed "
              "(pip install playwright && python -m playwright install chromium)")
        sys.exit(1 if strict else 0)

    if "--base" in sys.argv:
        base = sys.argv[sys.argv.index("--base") + 1].rstrip("/") + "/"
        print(f"phone layout check: {base}")
        failures = run(base, shots)
    else:
        if not (DEPLOY / "index.html").exists():
            sys.exit("site/deploy/index.html missing: run scripts/build_deploy.py first")
        with local_server(DEPLOY) as base:
            print(f"phone layout check: site/deploy via {base}")
            failures = run(base, shots)
    print()
    if failures:
        sys.exit(f"{failures} phone layout check(s) FAILED")
    print("phone layout ok")


if __name__ == "__main__":
    main()
