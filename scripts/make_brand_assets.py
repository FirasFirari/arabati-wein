r"""Brand images for the site and the Facebook Page.

    python scripts\make_brand_assets.py

Writes into site/static (deployed):
  og.png 1200x630            social share card (og:image / twitter:image)
  favicon.ico, icon-192.png, icon-512.png, apple-touch-icon.png, site.webmanifest
and into social/ (NOT deployed):
  fb-profile.png 1024x1024   Page profile picture
  fb-cover.png 1640x856      Page cover photo

The cards with Arabic text are rendered by Playwright's Chromium (Pillow has
no Arabic shaping here); icons are Pillow resizes of funding/arabati-wein-logo.png.
"""
import base64
import json
import pathlib
import sys

from PIL import Image
from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
LOGO = ROOT / "funding" / "arabati-wein-logo.png"
STATIC = ROOT / "site" / "static"
SOCIAL = ROOT / "social"
TEAL = (15, 76, 92)

FONTS = ("https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;700"
         "&family=Amiri:wght@700&display=swap")


def logo_uri() -> str:
    return "data:image/png;base64," + base64.b64encode(LOGO.read_bytes()).decode()


def card_html(w: int, h: int, kind: str) -> str:
    logo = logo_uri()
    if kind == "og":
        body = f"""
        <div class="row">
          <img src="{logo}" class="logo" style="width:300px;height:300px">
          <div class="txt">
            <h1>عربتي وين؟</h1>
            <p class="s">ابحث عن عربتك المفقودة في حرب السودان<br>برقم الشاسي أو اللوحة — مجاناً</p>
            <p class="u">mycarsd.online</p>
          </div>
        </div>"""
    elif kind == "cover":
        body = f"""
        <!-- Facebook centres the profile picture over the bottom-middle of the
             cover and crops the top/bottom on desktop, so the text sits on the
             right, the logo on the left, and the centre column stays clear. -->
        <div class="row" style="width:100%;justify-content:space-between;align-self:flex-start;margin-top:60px;padding:0 90px">
          <div class="txt">
            <h1 style="font-size:116px">عربتي وين؟</h1>
            <p class="s" style="font-size:40px">سجل مجاني للعربات المفقودة في حرب السودان<br>ابحث برقم الشاسي أو اللوحة</p>
            <p class="u" style="font-size:44px">mycarsd.online</p>
          </div>
          <img src="{logo}" class="logo" style="width:300px;height:300px">
        </div>"""
    else:
        raise ValueError(kind)
    return f"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<link rel="stylesheet" href="{FONTS}">
<style>
html,body{{margin:0;width:{w}px;height:{h}px;overflow:hidden}}
body{{background:linear-gradient(135deg,#0F4C5C 0%,#146C7C 60%,#1A7F8E 100%);color:#fff;
  font-family:"IBM Plex Sans Arabic","Segoe UI",sans-serif;display:flex;align-items:center;justify-content:center}}
.row{{display:flex;align-items:center;gap:54px;padding:0 70px}}
.logo{{border-radius:60px;box-shadow:0 12px 40px rgba(0,0,0,.35)}}
.txt{{text-align:right}}
h1{{font-family:Amiri,serif;font-size:118px;margin:0;line-height:1.1;color:#fff}}
.s{{font-size:40px;margin:14px 0 0;line-height:1.45;color:#EAF4F6;font-weight:500}}
.u{{font-size:42px;margin:22px 0 0;color:#F0B23C;font-weight:700;font-family:"IBM Plex Sans Arabic",monospace;direction:ltr}}
</style></head><body>{body}</body></html>"""


def render(page, w, h, kind, out: pathlib.Path):
    page.set_viewport_size({"width": w, "height": h})
    page.set_content(card_html(w, h, kind), wait_until="networkidle")
    page.evaluate("document.fonts.ready")
    page.wait_for_timeout(800)
    page.screenshot(path=str(out), clip={"x": 0, "y": 0, "width": w, "height": h})
    print(f"{out.relative_to(ROOT)}  {w}x{h}  {out.stat().st_size:,} bytes")


def icons():
    src = Image.open(LOGO).convert("RGBA")
    # Flatten onto teal so the rounded corners never show as a transparent hole.
    flat = Image.new("RGBA", src.size, TEAL + (255,))
    flat.alpha_composite(src)
    flat = flat.convert("RGB")
    for name, size in (("icon-512.png", 512), ("icon-192.png", 192), ("apple-touch-icon.png", 180)):
        flat.resize((size, size), Image.LANCZOS).save(STATIC / name, optimize=True)
        print(f"site/static/{name}  {size}x{size}")
    flat.save(STATIC / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    print("site/static/favicon.ico  16/32/48")
    flat.resize((1024, 1024), Image.LANCZOS).save(SOCIAL / "fb-profile.png", optimize=True)
    print("social/fb-profile.png  1024x1024")
    (STATIC / "site.webmanifest").write_text(json.dumps({
        "name": "عربتي وين؟ — Arabati Wein", "short_name": "عربتي وين؟",
        "description": "سجل مجاني للعربات المفقودة في حرب السودان — ابحث برقم الشاسي أو اللوحة",
        "start_url": "/?utm_source=pwa", "display": "standalone", "lang": "ar", "dir": "rtl",
        "background_color": "#F8F4EC", "theme_color": "#146C7C",
        "icons": [{"src": "/icon-192.png", "sizes": "192x192", "type": "image/png"},
                  {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png"}],
    }, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print("site/static/site.webmanifest")


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    SOCIAL.mkdir(exist_ok=True)
    icons()
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(device_scale_factor=1)
        render(page, 1200, 630, "og", STATIC / "og.png")
        render(page, 1640, 856, "cover", SOCIAL / "fb-cover.png")
        b.close()


if __name__ == "__main__":
    main()
