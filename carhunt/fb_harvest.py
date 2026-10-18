"""Self-hosted Facebook group and profile harvester — a free alternative to Apify.

Uses your own logged-in Chrome profile via Playwright, so there are no
per-post fees. It deliberately harvests through Facebook's IN-GROUP SEARCH
rather than the feed: the feed stalls under automation (Facebook throttles
pagination), while search reliably renders full results and lets us target
exactly the posts that matter — recovery lists and Corollas — instead of
paying to download a group's entire chatter.

Quality notes vs Apify:
  + expands "See more", so list posts arrive complete rather than truncated
  + targeted queries reach OLD posts a most-recent-N scrape never sees
  + no 1000-post ceiling per group
  - slower, and needs this machine awake
  - uses your real session, so it is paced deliberately and stays read-only

Groups and PROFILES are both harvested. A profile (a recovery page posting
under a person's or page's own timeline) has no in-page search, so profiles
are timeline-only — targeted queries apply to groups.

Usage:
    # close Chrome first — a profile can only be driven by one browser
    python -m carhunt.fb_harvest --groups all --queries default
    python -m carhunt.fb_harvest --groups 1299448381436574 --queries كشف,كورولا
    python -m carhunt.fb_harvest --profiles all --groups none   # profiles only
    python -m carhunt.fb_harvest --identifiers        # hunt our own car only
"""
import argparse
import asyncio
import hashlib
import json
import random
import re
import sys

from . import db
from .analyzer import sniff_media_type
from .config import MEDIA_DIR
from .config import PROJECT_ROOT, load_config

PROFILE_DIR = PROJECT_ROOT / "data" / "browser_profile"

# what actually surfaces vehicles: recovery lists, sightings, and the model
DEFAULT_QUERIES = ["كشف", "حصر", "موجودة", "مفقودة", "كورولا", "كرولا", "شاسي"]

# Facebook VIRTUALISES the feed: posts scrolled past are deleted from the DOM.
# So we cannot scroll to the bottom and then read — we must harvest
# incrementally, accumulating into a page-level store that survives the
# recycling. This installs that store once per page.
FEED_COLLECTOR_JS = r"""
() => {
  if (window.__ch) return 'already';
  window.__ch = new Map();
  window.__chCollect = () => {
    document.querySelectorAll('div[role="button"]').forEach(b => {
      const t = (b.textContent || '').trim();
      if (t === 'See more' || t === 'عرض المزيد') b.click();
    });
    const feed = document.querySelector('[role="feed"]');
    if (!feed) return 0;
    const clean = el => (el.innerText || '').split('\n')
      .map(l => l.trim())
      .filter(l => l.length > 2 && l !== 'Facebook' && l !== 'Follow' &&
                   l !== 'Join' && !l.startsWith('Comment as') &&
                   l !== 'See more' && l !== 'See less' && l !== 'All reactions:')
      .join('\n');
    for (const child of feed.children) {
      const text = clean(child);
      if (text.length < 60) continue;
      const a = [...child.querySelectorAll('a')]
        .map(x => x.href)
        .find(h => /\/posts\/|\/permalink\/|multi_permalinks/.test(h));
      const link = a ? a.split('?')[0] : null;
      const key = link || text.slice(0, 120);
      const prev = window.__ch.get(key);
      // keep the fullest version seen (a post may be captured mid-expansion)
      if (!prev || text.length > prev.text.length) {
        window.__ch.set(key, { text, link });
      }
    }
    return window.__ch.size;
  };
  return 'installed';
}
"""

DRAIN_JS = "() => [...window.__ch.values()]"

# Clicking "See more" and reading in the same call returns the OLD text —
# React has not re-rendered yet. Expand first, wait, then extract.
EXPAND_JS = r"""
() => {
  let n = 0;
  document.querySelectorAll('div[role="button"], span[role="button"]').forEach(b => {
    const t = (b.textContent || '').trim();
    if (t === 'See more' || t === 'عرض المزيد' || t === '…See more') { b.click(); n++; }
  });
  return n;
}
"""

# A post's own permalink is its TIMESTAMP link, and Facebook leaves that as a
# placeholder ("?__cft__..." -> the bare group URL) until the mouse moves over
# it. Nothing hovered, so 87-90% of Facebook posts from late August to
# 2026-10-08 were stored with no link. The ~10% that had one only got it
# because a rendered COMMENT carried the post id. A scripted
# hover on every placeholder fills them all in at once (measured: 5/5 feed
# posts, and search-page results too) with no mouse movement or scrolling.
# On a search or profile page the placeholder resolves to THAT page's path,
# not the group's, hence the location.pathname test.
REVEAL_LINKS_JS = r"""
() => {
  let n = 0;
  for (const a of document.querySelectorAll('a[href]')) {
    let u;
    try { u = new URL(a.href); } catch (e) { continue; }
    const raw = a.getAttribute('href') || '';
    const placeholder = raw.startsWith('?') || raw === '#' ||
      /^\/groups\/[^/]+\/?$/.test(u.pathname) || u.pathname === location.pathname;
    if (!placeholder || /\/(posts|permalink|user|photo|stories)\//.test(u.pathname)) continue;
    for (const type of ['pointerover', 'mouseover', 'focusin']) {
      const Ev = type[0] === 'p' ? PointerEvent : type[0] === 'm' ? MouseEvent : FocusEvent;
      a.dispatchEvent(new Ev(type, { bubbles: true, relatedTarget: document.body }));
    }
    n++;
  }
  return n;
}
"""


async def reveal_links(page) -> None:
    """Fill in placeholder permalinks before reading posts. See REVEAL_LINKS_JS."""
    try:
        if await page.evaluate(REVEAL_LINKS_JS):
            await page.wait_for_timeout(600)
    except Exception:
        pass   # a context loss here is handled by the extract that follows


# Facebook serves post photos from scontent.*.fbcdn.net; profile thumbnails,
# reaction icons and emoji come from static.*.fbcdn.net or arrive tiny. Only
# real post photos are worth the disk and the vision pass.
MAX_IMAGES_PER_POST = 6
MIN_IMAGE_BYTES = 8000

_EXT = {"image/jpeg": ".jpg", "image/png": ".png",
        "image/webp": ".webp", "image/gif": ".gif"}


async def download_post_images(page, urls: list[str]) -> list[str]:
    """Save a post's photos locally and return their paths.

    Fetched through the page's own request context so the logged-in session's
    cookies apply — an unauthenticated fetch of a scontent URL returns an HTML
    login page, which `sniff_media_type` then rejects on magic bytes rather
    than letting a .jpg full of HTML reach the vision model.
    """
    paths: list[str] = []
    for url in urls[:MAX_IMAGES_PER_POST]:
        try:
            resp = await page.request.get(url, timeout=20000)
            if not resp.ok:
                continue
            body = await resp.body()
        except Exception as e:
            print(f"    image fetch failed: {str(e)[:80]}")
            continue
        if len(body) < MIN_IMAGE_BYTES:
            continue
        mime = sniff_media_type(body[:16])
        if mime not in _EXT:
            continue
        name = hashlib.sha1(url.encode("utf-8")).hexdigest()[:20] + _EXT[mime]
        dest = MEDIA_DIR / name
        try:
            if not dest.exists():
                dest.write_bytes(body)
            paths.append(str(dest))
        except OSError as e:
            print(f"    image save failed: {e}")
    return paths

EXTRACT_JS = r"""
() => {
  // Post photos live on scontent.*.fbcdn.net. Two traps here:
  //  * Facebook paints a blurred PLACEHOLDER first (stp=..._fb50_s320x320_...,
  //    320px of actual pixels) and swaps in the real image (stp=dst-jpg_tt6,
  //    640px) once it loads. Both stay in the DOM.
  //  * Both are stretched to the same CSS width, so measuring i.width says
  //    1026 for a 320px image. Only naturalWidth is the truth, and filtering
  //    on the CSS size captured placeholders — 273x590 thumbnails far too
  //    small to read a door tag, which is the whole point of keeping photos.
  // So: judge on natural pixels, and when the same photo appears twice keep
  // the bigger copy. Rewriting the URL to ask for full size does NOT work —
  // the CDN signs it against the exact stp value and returns an error.
  const photoKey = u => (u.match(/\/([0-9]{6,}_[0-9]+_[0-9]+)/) || [0, u])[1];
  const imgs = el => {
    const best = new Map();
    for (const i of el.querySelectorAll('img')) {
      const src = i.src || '';
      if (!/scontent[^/]*\.fbcdn\.net/.test(src)) continue;
      const w = i.naturalWidth || 0, h = i.naturalHeight || 0;
      if (w < 400 || h < 300) continue;          // placeholders and avatars
      const k = photoKey(src);
      const prev = best.get(k);
      if (!prev || w * h > prev.px) best.set(k, {url: src, px: w * h});
    }
    return [...best.values()].map(v => v.url);
  };
  // search results and feeds both use role=feed; fall back to articles
  const feed = document.querySelector('[role="feed"]');
  if (!feed) {
    const arts = [...document.querySelectorAll('[role="article"]')]
      .filter(a => !a.parentElement.closest('[role="article"]'));
    const cl = el => (el.innerText || '').split('\n').map(l => l.trim())
      .filter(l => l.length > 2 && l !== 'Facebook' && l !== 'Follow' &&
                   l !== 'Join' && !l.startsWith('Comment as') &&
                   l !== 'See more' && l !== 'See less')
      .join('\n');
    return arts.map(a => {
      const lk = [...a.querySelectorAll('a')].map(x => x.href)
        .find(h => /\/posts\/|\/permalink\/|multi_permalinks/.test(h));
      return { text: cl(a), link: lk ? lk.split('?')[0] : null, images: imgs(a) };
    }).filter(p => p.text.length > 60 || p.images.length > 0);
  }
  const clean = el => (el.innerText || '').split('\n')
    .map(l => l.trim())
    .filter(l => l.length > 2 && l !== 'Facebook' && l !== 'Follow' &&
                 l !== 'Join' && !l.startsWith('Comment as') &&
                 l !== 'See more' && l !== 'See less')
    .join('\n');
  // Return the post's canonical URL. The identifier may live in the PATH or
  // in the QUERY (multi_permalinks=, story_fbid=) — stripping the query in
  // the latter case would collapse every post onto the bare group URL.
  const permalink = c => {
    for (const a of c.querySelectorAll('a[href]')) {
      const h = a.href;
      let m = h.match(/\/groups\/(\d+)\/(?:posts|permalink)\/(\d+)/);
      if (m) return `https://www.facebook.com/groups/${m[1]}/posts/${m[2]}/`;
      m = h.match(/\/(?:posts|permalink)\/(\d+)/);
      if (m) return h.split('?')[0];
      m = h.match(/[?&](?:multi_permalinks|story_fbid)=(\d+)/);
      if (m) {
        const g = h.match(/\/groups\/(\d+)/);
        return g ? `https://www.facebook.com/groups/${g[1]}/posts/${m[1]}/`
                 : `https://www.facebook.com/${m[1]}/`;
      }
    }
    return null;
  };
  return [...feed.children].map(c => ({
    text: clean(c), link: permalink(c), images: imgs(c),
  // A post carrying a photo is kept whatever its caption says: a photographed
  // كشف sheet is often posted with three words, and the caption length filter
  // used to discard exactly that post. Text-only posts still need >40 chars.
  })).filter(p => p.text.length > 40 || p.images.length > 0);
}
"""


# Profile/page timelines need their own extractor — the group one finds
# nothing on them. Verified against the "فتش لي عربيتي" page (162K):
#   * there is NO role="feed" and the posts are NOT role="article"; a post's
#     caption is the only reliably tagged part of it (see CAPTION_SEL)
#   * the posts are REELS, and a reel's permalink appears on the page only as
#     the context link of one of its comments (/reel/<id>/?comment_id=...) —
#     so comment anchors are the way to a post's own id
#   * role="article" nodes ARE the comments, and on a recovery page each one
#     is a lost-car report with a chassis number and a phone, i.e. exactly the
#     data we are collecting. Both are harvested.
# Facebook decorates every link with per-load __cft__/__tn__ tokens, so links
# are canonicalised down to the identifying params — leaving them in would
# make a new source_ref for the same post on every single sweep.
CAPTION_SEL = ('[data-ad-rendering-role="story_message"], '
               '[data-ad-comet-preview="message"]')

PROFILE_EXTRACT_JS = r"""
() => {
  const CAPTION_SEL = '[data-ad-rendering-role="story_message"], ' +
                      '[data-ad-comet-preview="message"]';
  const NOISE = new Set(['Facebook', 'Follow', 'Join', 'Like', 'Share',
                         'Reply', 'See more', 'See less', 'See translation',
                         'All reactions:', 'Active', 'Author', 'by Author']);
  const clean = el => (el.innerText || '').split('\n')
    .map(l => l.trim())
    .filter(l => l.length > 2 && !NOISE.has(l) && !l.startsWith('Comment as') &&
                 !l.startsWith('See Original') && !l.startsWith('View more'))
    .join('\n');

  const POSTID = /\/reel\/(\d+)|\/videos\/(\d+)|\/posts\/([\w-]+)|[?&]story_fbid=([\w-]+)/;
  const idOf = h => { const m = h.match(POSTID);
                      return m ? (m[1] || m[2] || m[3] || m[4]) : null; };
  // withComment=false for a post: a reel's permalink is only ever reachable
  // through one of its comments' context links, and keeping that comment_id
  // would key the POST by whichever comment happened to render — and collide
  // it with that comment's own row, silently dropping one of the two.
  const canon = (h, withComment) => {
    try {
      const u = new URL(h), keep = new URLSearchParams();
      const wanted = withComment ? ['story_fbid', 'id', 'comment_id']
                                 : ['story_fbid', 'id'];
      for (const k of wanted) {
        const v = u.searchParams.get(k);
        if (v) keep.set(k, v);
      }
      const qs = keep.toString();
      return 'https://www.facebook.com' + u.pathname + (qs ? '?' + qs : '');
    } catch (e) { return h.split('?')[0]; }
  };

  const out = [];

  // 1. post captions — the message body, so no comment text bleeds in
  for (const msg of document.querySelectorAll(CAPTION_SEL)) {
    const text = clean(msg);
    if (text.length < 25) continue;
    let el = msg, link = null;
    for (let i = 0; i < 10 && el.parentElement; i++) {
      el = el.parentElement;
      const found = new Map();
      for (const a of el.querySelectorAll('a[href]')) {
        const id = idOf(a.href);
        if (id) found.set(id, a.href);
      }
      // exactly one post id in scope means it is THIS post's; two means the
      // climb reached a neighbour, so fall back to hashing the text instead
      // of risking two posts sharing one key
      if (found.size === 1) { link = canon([...found.values()][0], false); break; }
      if (found.size > 1) break;
    }
    out.push({ text, link, kind: 'post' });
  }

  // 2. comments — each is its own report; keyed by comment_id, which is
  //    stable, unlike text that grows as "See more" expands
  for (const art of document.querySelectorAll('[role="article"]')) {
    const hrefs = [...art.querySelectorAll('a[href]')].map(a => a.href)
      .filter(h => /[?&]comment_id=/.test(h));
    if (!hrefs.length) continue;
    const text = clean(art);
    if (text.length < 25) continue;
    // prefer the link that names the parent post over the commenter's profile
    const href = hrefs.find(h => idOf(h)) || hrefs[0];
    out.push({ text, link: canon(href, true), kind: 'comment' });
  }
  return out;
}
"""


def post_ident(link: str | None, text: str) -> str | None:
    """Stable dedup key for one harvested item.

    A comment's key is its comment_id, not the tail of its URL: the tail is
    the parent post, so every comment on one reel would collapse onto a
    single ref and all but the first would be thrown away.
    """
    if link:
        comment = re.search(r"[?&]comment_id=([^&]+)", link)
        if comment:
            return f"c{comment.group(1)}"
        return link.rstrip("/").split("/")[-1] or None
    return db.content_hash(text)


def profile_url(profile_id: str) -> str:
    """Timeline URL for a numeric profile id or a vanity handle."""
    pid = str(profile_id).strip().lstrip("@")
    return (f"https://www.facebook.com/profile.php?id={pid}" if pid.isdigit()
            else f"https://www.facebook.com/{pid}")


def configured_groups(cfg: dict) -> list[str]:
    return [str(g["id"]) for g in (cfg.get("facebook", {}).get("groups") or [])]


def configured_profiles(cfg: dict) -> list[str]:
    return [str(p["id"]) for p in (cfg.get("facebook", {}).get("profiles") or [])]


async def harvest_feed(page, group_id: str, scrolls: int, pause: float,
                       want_media: bool = True) -> int:
    """Walk a group's whole chronological feed and store every post."""
    # plain group URL: the ?sorting_setting= form triggers a client-side
    # redirect that destroys the JS context right after load
    return await harvest_timeline(
        page, f"https://www.facebook.com/groups/{group_id}", group_id,
        scrolls, pause, EXTRACT_JS, '[role="feed"]', want_media)


async def harvest_profile(page, profile_id: str, scrolls: int,
                          pause: float, want_media: bool = True) -> int:
    """Walk a profile's or page's own timeline and store every post."""
    return await harvest_timeline(
        page, profile_url(profile_id), str(profile_id),
        scrolls, pause, PROFILE_EXTRACT_JS,
        f'[role="article"], {CAPTION_SEL}', want_media)


async def harvest_timeline(page, url: str, source_id: str, scrolls: int,
                           pause: float, extract_js: str,
                           ready_selector: str, want_media: bool = True) -> int:
    """Walk a chronological timeline (group feed or profile) and store posts.

    Extracts after each scroll rather than at the end, because Facebook
    removes off-screen posts from the DOM as you go.
    """
    group_id = source_id      # keeps the log lines below readable
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=90000)
        # let Facebook finish its own redirects/hydration before we touch it
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass
    except Exception as e:
        print(f"  [{group_id}] feed navigation failed: {e}")
        return 0
    # the feed is lazy: give it real time to render before touching anything.
    # what counts as "rendered" differs by page type, hence ready_selector.
    try:
        await page.wait_for_selector(ready_selector, timeout=45000)
    except Exception:
        print(f"  [{group_id}] no feed element appeared")
    await page.wait_for_timeout(int(pause * 3000))

    # Accumulate in PYTHON, not in the page: Facebook's client-side navigation
    # can recreate the JS context at any moment and take a page-side store with
    # it. Extract what is rendered on every step and merge here instead.
    collected: dict[str, dict] = {}
    stalls = context_losses = 0
    for step in range(scrolls):
        try:
            # expand first, let React re-render, then read the full text
            expanded = await page.evaluate(EXPAND_JS)
            if expanded:
                await page.wait_for_timeout(1500)
            await reveal_links(page)
            batch = await page.evaluate(extract_js)
            rendered = await page.evaluate(
                "() => { const f = document.querySelector('[role=\"feed\"]');"
                " return f ? f.children.length : document.querySelectorAll("
                "'[role=\"article\"], [data-ad-rendering-role=\"story_message\"]'"
                ").length; }")
        except Exception as e:
            msg = str(e)
            if "Execution context was destroyed" in msg or "navigating" in msg:
                # Facebook re-rendered under us; wait for the new context and
                # carry on — everything collected so far is safe in Python
                context_losses += 1
                if context_losses > 12:
                    print(f"  [{group_id}] page kept re-navigating, giving up")
                    break
                await page.wait_for_timeout(int(pause * 2000))
                continue
            print(f"  [{group_id}] extract failed at step {step}: {e}")
            break

        before = len(collected)
        for post in batch:
            # A post can render once before its link is revealed and once
            # after. Keyed apart, the unlinked copy would be stored too, as a
            # linkless duplicate. Fold the two together and keep the link.
            tkey = post["text"][:120]
            if post["link"]:
                collected.pop(tkey, None)
            elif any(p["link"] and p["text"][:120] == tkey
                     for p in collected.values()):
                continue
            key = post["link"] or tkey
            prev = collected.get(key)
            if not prev or len(post["text"]) > len(prev["text"]):
                collected[key] = post   # keep the fullest version seen

        if len(collected) == before:
            stalls += 1
            if stalls >= 8:
                print(f"  [{group_id}] feed stopped yielding "
                      f"({len(collected)} collected, {rendered} rendered)")
                break
        else:
            stalls = 0

        # scroll the document itself — a synthetic wheel event over the page
        # does not reliably drive Facebook's infinite loader
        await page.evaluate("() => window.scrollBy(0, window.innerHeight * 2)")
        await page.wait_for_timeout(int((pause + random.uniform(0, 1.2)) * 1000))
        if stalls:  # nudge a stalled loader in a couple of different ways
            await page.evaluate(
                "() => window.scrollTo(0, document.body.scrollHeight)")
            await page.keyboard.press("End")
            await page.wait_for_timeout(int(pause * 1500))
        if step % 5 == 0:
            print(f"  [{group_id}] step {step}: {len(collected)} collected, "
                  f"{rendered} rendered", flush=True)

    posts = list(collected.values())
    added = 0
    images = 0
    linkless = filled = 0
    kinds: dict[str, int] = {}
    for post in posts:
        text = post["text"]
        ident = post_ident(post["link"], text)
        if not ident:
            continue
        ref = f"fb:{group_id}:{ident}"
        if db.has_post(ref):
            continue
        if fill_missing_link(group_id, post):
            filled += 1
            continue
        if collapsed_copy(group_id, post, posts):
            continue
        media = (await download_post_images(page, post.get("images") or [])
                 if want_media else [])
        if db.add_post(f"facebook:{group_id}", ref, post["link"], text,
                       media) is not None:
            added += 1
            images += len(media)
            linkless += not post["link"]
            kind = post.get("kind") or "post"
            kinds[kind] = kinds.get(kind, 0) + 1
    detail = " (" + ", ".join(f"{n} {k}" for k, n in sorted(kinds.items())) + ")" \
        if len(kinds) > 1 else ""
    print(f"  [{group_id}] feed: {len(posts)} seen, {added} new{detail}"
          + (f", {images} photos" if images else "")
          + (f", {linkless} WITHOUT LINK" if linkless else "")
          + (f", {filled} old post(s) got their link" if filled else ""))
    return added


_SEE_MORE_TAIL = re.compile(r"\s*…?\s*See more\s*$")


def collapsed_copy(group_id: str, post: dict, batch: list[dict]) -> bool:
    """Is this a linkless, COLLAPSED re-showing of a post we already hold?

    Facebook re-surfaces old shares as a collapsed card ("( كشف جديد 368 ) …
    See more") that never expands and never reveals its link. Its text then
    hashes differently from the full post, so it used to be stored again as a
    short linkless duplicate: 2,089 of them by 2026-10-08. Skip it when the
    full post (text starting with the collapsed text) is already stored in
    this group or is in the same batch. The "See more" marker is often gone
    already (`clean` drops it when it sits on its own line), so a collapsed
    copy is recognised by being a strict prefix of a longer post, not by it.
    """
    if post.get("link"):
        return False
    stem = _SEE_MORE_TAIL.sub("", post["text"]).rstrip()
    if len(stem) < 40:
        return False
    if any(p is not post and len(p["text"]) > len(stem)
           and p["text"].startswith(stem) for p in batch):
        return True
    return db.has_text_prefix(f"facebook:{group_id}", stem)


def fill_missing_link(group_id: str, post: dict) -> bool:
    """Give an already-stored, linkless copy of this post its link.

    Posts stored before the links were revealed are keyed by a hash of their
    text (`fb:<group>:<hash>`). Seen again with a link, they would get a NEW
    key and be inserted a second time. So find the hashed row, fill in its
    url, and store nothing new. Returns True if a row was updated.
    """
    if not post.get("link"):
        return False
    digest = db.content_hash(post["text"])
    return bool(digest) and db.fill_url(f"fb:{group_id}:{digest}", post["link"])


async def harvest_group(page, group_id: str, queries: list[str],
                        scrolls: int, pause: float,
                        want_media: bool = True) -> int:
    """Store new posts found by searching this group. Returns count added."""
    added = 0
    for query in queries:
        url = (f"https://www.facebook.com/groups/{group_id}/search/"
               f"?q={query}")
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"  [{group_id}] '{query}' navigation failed: {e}")
            continue
        await page.wait_for_timeout(int(pause * 1000))

        seen_refs = set()
        for step in range(scrolls):
            await reveal_links(page)
            try:
                posts = await page.evaluate(EXTRACT_JS)
            except Exception as e:
                print(f"  [{group_id}] '{query}' extract failed: {e}")
                break
            for post in posts:
                text = post["text"]
                ref = (f"fb:{group_id}:"
                       + (post["link"].rstrip("/").split("/")[-1]
                          if post["link"] else db.content_hash(text) or ""))
                if not ref.endswith(":") and ref not in seen_refs:
                    seen_refs.add(ref)
                    if (not db.has_post(ref)
                            and not fill_missing_link(group_id, post)
                            and not collapsed_copy(group_id, post, posts)):
                        media = await download_post_images(
                            page, post.get("images") or []) if want_media else []
                        if db.add_post(f"facebook:{group_id}", ref,
                                       post["link"], text, media) is not None:
                            added += 1
            await page.mouse.wheel(0, 3000)
            # human-ish pacing: this is a real account, don't hammer it
            await page.wait_for_timeout(int((pause + random.uniform(0, 1.5)) * 1000))
        print(f"  [{group_id}] '{query}': {len(seen_refs)} posts seen")
    return added


async def run(group_ids: list[str], queries: list[str], scrolls: int,
              pause: float, mode: str, profile_ids: list[str] | None = None) -> None:
    from playwright.async_api import async_playwright

    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            headless=False,          # Facebook is hostile to headless
            viewport={"width": 1280, "height": 900},
            args=["--disable-blink-features=AutomationControlled"],
        )
        page = context.pages[0] if context.pages else await context.new_page()

        await page.goto("https://www.facebook.com/", wait_until="domcontentloaded")
        await page.wait_for_timeout(4000)
        if "login" in page.url or "checkpoint" in page.url:
            print("\nNot logged in yet in this profile.\n"
                  "A browser window is open — log into Facebook there, then "
                  "re-run this command. The session is saved to "
                  f"{PROFILE_DIR} and reused from then on.")
            await page.wait_for_timeout(120000)
            await context.close()
            return

        total = 0
        for gid in group_ids:
            print(f"[{gid}] harvesting ({mode})...", flush=True)
            if mode in ("feed", "both"):
                total += await harvest_feed(page, gid, scrolls, pause)
            if mode in ("search", "both"):
                total += await harvest_group(page, gid, queries, scrolls, pause)
            await page.wait_for_timeout(int(pause * 2000))

        # profiles have no in-page search, so they are timeline-only whatever
        # the mode says
        for pid in profile_ids or []:
            print(f"[profile {pid}] harvesting timeline...", flush=True)
            try:
                total += await harvest_profile(page, pid, scrolls, pause)
            except Exception as e:
                print(f"  [{pid}] failed: {str(e)[:180]}")
            await page.wait_for_timeout(int(pause * 2000))

        print(f"\nDone — {total} new posts stored. "
              f"Now run: python -m carhunt.ingest reanalyze")
        await context.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--groups", default="all",
                        help="'all', 'none', or comma-separated group ids")
    parser.add_argument("--profiles", default="none",
                        help="'all', 'none', or comma-separated profile ids "
                             "or handles (timeline only — profiles have no "
                             "in-page search)")
    parser.add_argument("--queries", default="default",
                        help="'default', or comma-separated search terms")
    parser.add_argument("--identifiers", action="store_true",
                        help="search only for this car's own identifiers")
    parser.add_argument("--scrolls", type=int, default=6,
                        help="scroll steps per query (feed mode: use 40+)")
    parser.add_argument("--pause", type=float, default=3.0,
                        help="base seconds between actions (be gentle)")
    parser.add_argument("--mode", choices=["feed", "search", "both"],
                        default="feed",
                        help="feed = walk the whole chronological feed; "
                             "search = targeted queries; both = feed then search")
    args = parser.parse_args()

    cfg = load_config()
    db.init()
    if args.groups == "all":
        group_ids = configured_groups(cfg)
    elif args.groups in ("none", ""):
        group_ids = []
    else:
        group_ids = args.groups.split(",")

    if args.profiles == "all":
        profile_ids = configured_profiles(cfg)
    elif args.profiles in ("none", ""):
        profile_ids = []
    else:
        profile_ids = args.profiles.split(",")

    if args.identifiers:
        car = cfg["car"]
        vin = (car.get("vin") or "").upper()
        queries = [vin, vin[-7:], vin[-6:],
                   (car.get("plate") or "").split("/")[0].strip()]
        queries = [q for q in queries if q]
    elif args.queries == "default":
        queries = DEFAULT_QUERIES
    else:
        queries = args.queries.split(",")

    extra = f" + {len(profile_ids)} profile(s)" if profile_ids else ""
    if args.mode == "feed":
        print(f"{len(group_ids)} group(s){extra}, FEED mode, "
              f"{args.scrolls} scrolls each\n")
    else:
        print(f"{len(group_ids)} group(s) x {len(queries)} queries "
              f"({args.mode} mode){extra}: {queries}\n")
    asyncio.run(run(group_ids, queries, args.scrolls, args.pause, args.mode,
                    profile_ids))


if __name__ == "__main__":
    main()
