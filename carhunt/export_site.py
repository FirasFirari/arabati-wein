"""Generate the shareable searchable-database page (site/arabati-wein.html)
from the vehicles table. Re-run after every extraction to refresh the data,
then republish the artifact.

Usage:
    python -m carhunt.export_site
    python -m carhunt.export_site --ui-only   # re-fill the template around the
                                              # CURRENT payload: no DB pass, no RAM,
                                              # core.bin/detail.bin stay byte-identical
"""
import base64
import datetime
import gzip
import hashlib
import json
import re

import sys

from . import db
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

TEMPLATE = r"""<!doctype html>
<html lang="ar" dir="rtl">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>عربتي وين؟ — ابحث عن عربتك المفقودة في السودان برقم الشاسي أو اللوحة</title>
<meta name="description" content="سجل عام مجاني للعربات المنهوبة والمفقودة في حرب السودان: ابحث برقم الشاسي أو اللوحة في مئات آلاف الرصدات من مجموعات العربات المفقودة، وسجّل تنبيهاً إن لم تجد رقمك. Arabati Wein: free registry of vehicles looted in the Sudan war — search by chassis (VIN) or plate.">
<meta name="robots" content="index,follow,max-image-preview:large">
<meta name="google-site-verification" content="-_wy_1e6CPT7JvsiJkBIEX4TUqWspxFAKwfRiZVmgMk">
<meta http-equiv="content-language" content="ar">
<meta name="theme-color" content="#146C7C">
<link rel="canonical" href="https://mycarsd.online/">
<link rel="alternate" hreflang="ar" href="https://mycarsd.online/">
<link rel="alternate" hreflang="en" href="https://mycarsd.online/en.html">
<link rel="alternate" hreflang="x-default" href="https://mycarsd.online/">
<link rel="icon" href="/favicon.ico" sizes="48x48">
<link rel="icon" href="/icon-192.png" type="image/png" sizes="192x192">
<link rel="apple-touch-icon" href="/apple-touch-icon.png">
<link rel="manifest" href="/site.webmanifest">
<meta property="og:type" content="website">
<meta property="og:title" content="عربتي وين؟ — ابحث عن عربتك المفقودة برقم الشاسي أو اللوحة">
<meta property="og:description" content="سجل عام مجاني للعربات المنهوبة والمفقودة في حرب السودان. Free registry of vehicles looted in the Sudan war — search by chassis or plate.">
<meta property="og:url" content="https://mycarsd.online/">
<meta property="og:site_name" content="Arabati Wein">
<meta property="og:locale" content="ar_SD">
<meta property="og:locale:alternate" content="en_GB">
<meta property="og:image" content="https://mycarsd.online/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta property="og:image:alt" content="عربتي وين؟ — ابحث عن عربتك المفقودة في حرب السودان برقم الشاسي أو اللوحة — mycarsd.online">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="عربتي وين؟ — Arabati Wein">
<meta name="twitter:description" content="سجل مجاني للعربات المفقودة في حرب السودان — ابحث برقم الشاسي أو اللوحة. Free Sudan looted-car registry.">
<meta name="twitter:image" content="https://mycarsd.online/og.png">
<script type="application/ld+json">{"@context":"https://schema.org","@graph":[{"@type":["Organization","NGO"],"@id":"https://mycarsd.online/#org","name":"Arabati Wein","alternateName":["عربتي وين؟","Arabati Wein Registry","mycarsd.online"],"url":"https://mycarsd.online/","logo":{"@type":"ImageObject","url":"https://mycarsd.online/icon-512.png","width":512,"height":512},"image":"https://mycarsd.online/og.png","description":"A free, scam-free public registry of vehicles looted or lost in the Sudan war. Families search by chassis or plate number, register a standing watch, and are routed to the official free recovery channels.","foundingDate":"2026-08","areaServed":{"@type":"Country","name":"Sudan"},"knowsLanguage":["ar","en"],"slogan":"Where is my car?","nonprofitStatus":"https://schema.org/NonprofitType","sameAs":["https://www.facebook.com/profile.php?id=61596074817604","https://github.com/FirasFirari/arabati-wein","https://opencollective.com/arabeti-ween","https://t.me/ArabatiWeinBot"]},{"@type":"WebSite","@id":"https://mycarsd.online/#website","url":"https://mycarsd.online/","name":"Arabati Wein","alternateName":"عربتي وين؟","inLanguage":["ar","en"],"description":"Free public registry of vehicles looted or lost in the Sudan war. Search by chassis (VIN) or plate number.","publisher":{"@id":"https://mycarsd.online/#org"},"potentialAction":{"@type":"SearchAction","target":{"@type":"EntryPoint","urlTemplate":"https://mycarsd.online/?q={search_term_string}"},"query-input":"required name=search_term_string"}},{"@type":"WebPage","@id":"https://mycarsd.online/#webpage","url":"https://mycarsd.online/","name":"عربتي وين؟ — ابحث عن عربتك المفقودة في السودان برقم الشاسي أو اللوحة","inLanguage":"ar","isPartOf":{"@id":"https://mycarsd.online/#website"},"about":{"@id":"https://mycarsd.online/#org"},"primaryImageOfPage":"https://mycarsd.online/og.png"}]}</script>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans+Arabic:wght@400;500;700&family=Amiri:wght@700&family=IBM+Plex+Mono:wght@500&display=swap">
<style>
:root {
  --bg: #F8F4EC; --surface: #FFFFFF; --ink: #23313B; --muted: #6E7B84;
  --accent: #146C7C; --accent-ink: #FFFFFF; --line: #E3DCCC;
  --match: #1D7A46; --match-bg: #E4F2E9; --mid-bg: #FBEEDA; --mid: #8A5A18;
  --danger: #A33B2E; --danger-bg: #F7E5E0;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #131A20; --surface: #1C2630; --ink: #E8E4DA; --muted: #93A0A8;
    --accent: #4FB3C5; --accent-ink: #0E1418; --line: #2C3945;
    --match: #6FCF97; --match-bg: #1C3327; --mid-bg: #3A2E17; --mid: #E3B36A;
    --danger: #E38A76; --danger-bg: #3A231D;
  }
}
:root[data-theme="dark"] {
  --bg: #131A20; --surface: #1C2630; --ink: #E8E4DA; --muted: #93A0A8;
  --accent: #4FB3C5; --accent-ink: #0E1418; --line: #2C3945;
  --match: #6FCF97; --match-bg: #1C3327; --mid-bg: #3A2E17; --mid: #E3B36A;
  --danger: #E38A76; --danger-bg: #3A231D;
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--ink);
  font-family: "IBM Plex Sans Arabic", "Segoe UI", Tahoma, sans-serif;
  line-height: 1.65;
}
.wrap { max-width: 760px; margin: 0 auto; padding: 28px 18px 60px; }
header h1 {
  font-family: Amiri, "IBM Plex Sans Arabic", serif;
  font-size: 2.6rem; margin: 0; text-wrap: balance; color: var(--accent);
}
header p.sub { margin: 4px 0 0; color: var(--muted); font-size: .95rem; }
.searchbox { margin: 20px 0 8px; }
/* .ask and .hint are <label for="q">: tapping the prompt focuses the box. */
.ask { display: block; margin: 0 0 8px; font-size: 1.06rem; font-weight: 600; color: var(--ink); }
.ask span { font-weight: 400; color: var(--muted); font-size: .92rem; }
details.filterwrap > summary {
  cursor: pointer; color: var(--accent); font-size: .9rem; margin: 10px 0 4px;
  list-style-position: inside;
}
/* The stats are context, not the product — they sit under the search now and
   are sized accordingly. */
.coverage { margin-top: 22px; opacity: .92; }
.searchbox input {
  width: 100%; padding: 14px 18px; font-size: 1.15rem; border-radius: 10px;
  border: 2px solid var(--accent); background: var(--surface); color: var(--ink);
  font-family: inherit; direction: rtl;
}
.searchbox input:focus { outline: 3px solid color-mix(in srgb, var(--accent) 35%, transparent); }
.hint { display: block; color: var(--muted); font-size: .85rem; margin: 8px 2px 0; }
.filters {
  display: flex; flex-wrap: wrap; gap: 10px; margin: 14px 0 2px;
  align-items: center;
}
.filters select, .filters input[type="number"] {
  font-family: inherit; font-size: .9rem; padding: 7px 10px;
  border-radius: 8px; border: 1px solid var(--line);
  background: var(--surface); color: var(--ink);
}
.filters label {
  display: inline-flex; align-items: center; gap: 6px;
  font-size: .88rem; color: var(--muted);
}
.filters .yearpair { display: inline-flex; align-items: center; gap: 4px; }
.filters .yearpair input { width: 5.2rem; }
.filters button {
  font-family: inherit; font-size: .88rem; padding: 7px 14px;
  border-radius: 8px; border: 1px solid var(--line);
  background: transparent; color: var(--accent); cursor: pointer;
}
.filters button:hover { border-color: var(--accent); }
.coverage {
  display: flex; flex-wrap: wrap; gap: 10px; margin: 18px 0 0;
  min-height: 82px;
}
#results { min-height: 60vh; }
.coverage .stat {
  flex: 1 1 8.5rem; background: var(--surface); border: 1px solid var(--line);
  border-radius: 10px; padding: 10px 14px;
}
.coverage .n {
  font-size: 1.5rem; font-weight: 700; color: var(--accent);
  font-variant-numeric: tabular-nums; line-height: 1.15;
}
.coverage .lbl { font-size: .8rem; color: var(--muted); }
details.sources {
  margin-top: 10px; border: 1px solid var(--line); border-radius: 10px;
  background: var(--surface); padding: 10px 14px;
}
details.sources summary {
  cursor: pointer; font-size: .9rem; color: var(--accent); font-weight: 500;
}
details.sources ul {
  list-style: none; margin: 10px 0 0; padding: 0;
  display: grid; grid-template-columns: repeat(auto-fill, minmax(15rem, 1fr));
  gap: 4px 16px;
}
details.sources li {
  display: flex; justify-content: space-between; gap: 10px;
  font-size: .85rem; color: var(--muted); border-bottom: 1px dotted var(--line);
  padding: 3px 0;
}
details.sources li b { color: var(--ink); font-weight: 500; }
details.sources li span { font-variant-numeric: tabular-nums; }
/* Clarity 2026-10-28: source names drew more dead taps than anything else on
   the page (8 of 28) — people expect the group or channel to open. Now it does. */
details.sources li a { color: var(--accent); font-weight: 500; text-decoration: none; }
details.sources li a:hover { text-decoration: underline; }
.searchbox.active .ask { color: var(--accent); }
.watch .consent.on span { color: var(--ink); }
.chipbar { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
.chipbar .f {
  font-size: .78rem; padding: 3px 10px; border-radius: 999px;
  background: var(--mid-bg); color: var(--mid);
}
.stats { color: var(--muted); font-size: .9rem; margin: 18px 2px 6px; }
.card {
  background: var(--surface); border: 1px solid var(--line); border-radius: 12px;
  padding: 16px 18px; margin: 12px 0;
}
.card .top { display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }
.card .veh { font-weight: 700; font-size: 1.08rem; }
.chip {
  font-size: .78rem; padding: 2px 10px; border-radius: 999px; white-space: nowrap;
  background: var(--mid-bg); color: var(--mid);
}
.chip.strong { background: var(--match-bg); color: var(--match); }
.chip.warn { background: var(--mid-bg); color: var(--mid); }
.idrow { display: flex; gap: 18px; flex-wrap: wrap; margin-top: 8px; }
.idrow div { font-size: .92rem; color: var(--muted); }
.idrow bdi {
  font-family: "IBM Plex Mono", Consolas, monospace; font-variant-numeric: tabular-nums;
  color: var(--ink); font-size: 1rem;
}
.desc { margin-top: 6px; font-size: .95rem; }
.srcline { margin-top: 10px; display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
.srcline a {
  color: var(--accent-ink); background: var(--accent); text-decoration: none;
  padding: 6px 14px; border-radius: 8px; font-size: .9rem; font-weight: 500;
}
.srcline span { color: var(--muted); font-size: .82rem; }
.empty { text-align: center; color: var(--muted); padding: 40px 10px; }
.pager {
  display: flex; flex-direction: column; align-items: center; gap: 8px;
  margin: 18px 0 4px;
}
.pager button {
  font-family: inherit; font-size: .95rem; font-weight: 500;
  padding: 10px 26px; border-radius: 10px; cursor: pointer;
  border: 1px solid var(--accent); background: var(--surface); color: var(--accent);
}
.pager button:hover { background: var(--accent); color: var(--accent-ink); }
.pager span { font-size: .84rem; color: var(--muted); font-variant-numeric: tabular-nums; }
/* Standing watch. Shown ONLY after a search returns nothing — which is what
   happens to ~95% of searchers (Clarity: 21 of 22 sessions). Without it a
   miss is a dead end; with it the visitor leaves a number we can match
   against every future ingest. */
.watch {
  margin: 18px 0; padding: 16px 18px; border-radius: 12px;
  border: 1px solid var(--line); background: var(--card);
}
.watch h2 { margin: 0 0 6px; font-size: 1.05rem; font-family: inherit; color: inherit; }
.vh { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0 0 0 0); white-space: nowrap; }
.watch p { margin: 6px 0 12px; color: var(--muted); font-size: .92rem; line-height: 1.6; }
.watch label { display: block; margin: 10px 0 4px; font-size: .9rem; font-weight: 600; }
.watch input[type=text], .watch input[type=tel] {
  width: 100%; padding: 10px 12px; border-radius: 8px;
  border: 1px solid var(--line); background: var(--bg); color: var(--fg);
  font: inherit; box-sizing: border-box;
}
.watch .consent { display: flex; gap: 8px; align-items: flex-start; margin: 14px 0 4px; }
.watch .consent input { margin-top: 3px; flex: 0 0 auto; }
.watch .consent span { font-size: .85rem; color: var(--muted); line-height: 1.5; }
.watch button {
  margin-top: 12px; padding: 11px 20px; border-radius: 8px; border: 0;
  background: var(--accent, #2563eb); color: #fff; font: inherit;
  font-weight: 600; cursor: pointer;
}
.watch button[disabled] { opacity: .55; cursor: not-allowed; }
.watch .hp { position: absolute; left: -9999px; }
.watch-msg { margin-top: 10px; font-size: .92rem; }
.watch-msg.ok { color: #15803d; }
.watch-msg.err { color: #b91c1c; }
.notice {
  margin-top: 40px; border: 1px solid var(--line); border-radius: 12px;
  padding: 14px 18px; font-size: .88rem; color: var(--muted); background: var(--surface);
}
.notice strong { color: var(--ink); }
#site-links a.fb, #site-links button.share {
  display: inline-block; margin: 4px 6px; padding: 8px 16px; border-radius: 8px;
  font: inherit; font-size: .92rem; text-decoration: none; cursor: pointer;
  background: var(--accent); color: var(--accent-ink); border: 1px solid var(--accent);
}
#site-links button.share { background: var(--surface); color: var(--accent); }
a.plain { color: var(--accent); }
#lang-toggle {
  float: left; font-family: inherit; font-size: .85rem; padding: 6px 16px;
  border-radius: 999px; border: 1px solid var(--line);
  background: var(--surface); color: var(--accent); cursor: pointer;
}
#lang-toggle:hover { border-color: var(--accent); }
.wrap[dir="ltr"] #lang-toggle { float: right; }
.tl-body .desc, .tl-top span, .veh { unicode-bidi: plaintext; }
/* ---- dossier timeline ---- */
.tl { list-style: none; margin: 10px 0 0; padding: 0; border-top: 1px solid var(--line); }
.tl-item { display: flex; gap: 12px; padding: 9px 0; border-bottom: 1px dotted var(--line); }
.tl-date {
  font-family: "IBM Plex Mono", Consolas, monospace; font-size: .82rem;
  color: var(--muted); white-space: nowrap; padding-top: 2px;
  font-variant-numeric: tabular-nums;
}
.tl-body { flex: 1; min-width: 0; }
.tl-top { display: flex; flex-wrap: wrap; gap: 6px 12px; align-items: center; font-size: .9rem; }
.tl-body .desc {
  margin: 4px 0 0; font-size: .88rem; color: var(--muted);
  display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
}
.tl-src { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-top: 4px; font-size: .82rem; }
.tl-src a { color: var(--accent); text-decoration: none; font-weight: 500; }
.tl-src .muted { color: var(--muted); }
details.tl-more > summary { cursor: pointer; color: var(--accent); font-size: .88rem; padding: 8px 0 2px; list-style-position: inside; }
details.tl-more .tl { border-top: none; margin-top: 0; }
.chip.count { background: color-mix(in srgb, var(--accent) 14%, transparent); color: var(--accent); }
.chip.danger { background: var(--danger-bg); color: var(--danger); }
/* Tap targets. Clarity (2026-10-13): ~36% of sessions had a dead click, and the
   taps were on the card itself — title, chips, date, description, source — by
   people who had just found a match and wanted "more". Every one of those now
   answers: a sighting row opens its post (or unfolds when it has no link), the
   card header unfolds every sighting, a ⚠️ chip explains itself in place. */
.card .top, .card .idrow, .tl-item, .coverage .stat, .chip[title] { cursor: pointer; }
.tl-item.open, .card.open .tl-item { background: color-mix(in srgb, var(--accent) 6%, transparent); }
.tl-item.open .desc, .card.open .tl-body .desc { -webkit-line-clamp: unset; display: block; }
.chip-note {
  margin: 6px 0 2px; font-size: .82rem; line-height: 1.5; color: var(--ink);
  background: var(--mid-bg); border-radius: 8px; padding: 6px 10px;
}
.tl-src .nolink { color: var(--muted); font-style: italic; }
/* core.bin answers first; detail.bin (the timelines) follows. */
.tl-wait { margin-top: 10px; font-size: .85rem; color: var(--muted); }
.detail-status { color: var(--muted); font-size: .82rem; margin: -2px 2px 6px; }
.contact-warn { margin-top: 8px; font-size: .8rem; color: var(--danger); }
.help { text-align: right; }
.help ol { margin: 10px 0 0; padding: 0 20px 0 0; display: grid; gap: 8px; }
.help a { color: var(--accent); }
.danger-box {
  margin-top: 12px; background: var(--danger-bg); color: var(--danger);
  border-radius: 10px; padding: 10px 14px; font-size: .88rem;
}
@media (prefers-reduced-motion: no-preference) {
  .card { animation: rise .25s ease both; }
  @keyframes rise { from { opacity: 0; transform: translateY(4px); } }
}
</style>
</head>
<body>
<div class="wrap" dir="rtl" lang="ar">
<header>
  <button id="lang-toggle" type="button">English</button>
  <h1>عربتي وين؟</h1>
  <p class="sub" id="sub">سجل مجتمعي للعربات المفقودة في حرب السودان — يجمع البلاغات والمشاهدات من المنشورات العامة.
  <span dir="ltr">Community registry of vehicles lost in the Sudan war.</span></p>
</header>

<!-- SEARCH FIRST. Clarity measured 7 SECONDS of active time on a landing
     page whose entire first screen was five stat tiles, a sources accordion
     and nine filter controls — a wall of furniture in front of the one thing
     a family came to do. The search box is now the first thing below the
     title; the stats and sources moved BELOW it, and the filters are folded
     away until asked for. -->
<div class="searchbox">
  <h2 class="vh" id="h-search">البحث في سجل العربات المفقودة برقم الشاسي أو اللوحة</h2>
  <label class="ask" id="ask" for="q">اكتب رقم الشاسي أو اللوحة
    <span dir="ltr">/ Enter your chassis or plate number</span></label>
  <!-- NOT disabled while the payload loads. It used to be, and on Sudanese
       mobile data the download takes a minute or more: Clarity recorded
       visitors tapping a dead box for a whole session and leaving. Typing is
       accepted at once; render() reads the box the moment the data lands. -->
  <input id="q" type="search" autocomplete="off"
    placeholder="مثال: 1122334 أو 12345 خ1" aria-label="بحث">
  <label class="hint" id="hint" for="q">يكفي جزء من الرقم. تقبل الأرقام العربية والإنجليزية. أو ابحث بالوصف: كورولا ابيض 2015.</label>

  <details class="filterwrap"><summary id="filters-summary">بحث متقدم — نوع، لون، سنة، موقع</summary>
  <div class="filters">
    <select id="f-model" aria-label="نوع العربة"><option value="">كل الأنواع</option></select>
    <select id="f-color" aria-label="اللون"><option value="">كل الألوان</option></select>
    <select id="f-status" aria-label="الحالة"><option value="">كل الحالات</option></select>
    <select id="f-location" aria-label="الموقع"><option value="">كل المواقع</option></select>
    <span class="yearpair">
      <input id="f-year-min" type="number" inputmode="numeric" placeholder="من سنة" aria-label="من سنة">
      <input id="f-year-max" type="number" inputmode="numeric" placeholder="إلى سنة" aria-label="إلى سنة">
    </span>
    <label><input id="f-chassis" type="checkbox"> <span id="lb-chassis">لها رقم شاسي</span></label>
    <label><input id="f-contact" type="checkbox"> <span id="lb-contact">لها رقم تواصل</span></label>
    <select id="f-sort" aria-label="الترتيب">
      <option value="best">الترتيب الأنسب</option>
      <option value="latest">الأحدث رصداً أولاً</option>
      <option value="oldest">الأقدم رصداً أولاً</option>
      <option value="most">الأكثر رصداً</option>
    </select>
    <label><input id="f-loose" type="checkbox"> <span id="lb-loose">عرض رصدات بلا أي رقم</span></label>
    <button id="f-reset" type="button">مسح الفلاتر</button>
  </div>
  </details>
  <div class="chipbar" id="chipbar"></div>
</div>

<h2 class="vh" id="h-coverage">تغطية السجل والمصادر التي يُفحص منها</h2>
<div class="coverage" id="coverage"></div>
<details class="sources">
  <summary id="sources-summary">المصادر التي يتم فحصها</summary>
  <ul id="sources-list"></ul>
</details>

<p class="stats" id="stats">جاري تحميل السجل…</p>
<p class="detail-status" id="detail-status" hidden></p>
<div id="results"></div>
<div class="pager">
  <button id="more" type="button" hidden>عرض المزيد</button>
  <span id="shown"></span>
</div>

<!-- STANDING WATCH.
     This form is STATIC and always present in the deployed HTML, merely
     hidden, because Netlify detects forms by parsing the file at deploy time
     — a form injected later by JavaScript is invisible to it and silently
     collects nothing. Do not move this into innerHTML.
     It is revealed only when a search returns zero results. -->
<div class="watch" id="watch-box" hidden>
  <h2>لم نجد الرقم — سجّل تنبيهاً</h2>
  <p>
    السجل يكبر يومياً. اترك رقم الشاسي أو اللوحة ووسيلة تواصل، وإذا ظهر هذا
    الرقم في أي منشور لاحقاً سنبلغك.
    <br><span dir="ltr">The registry grows daily. Leave the chassis or plate
    number and a way to reach you, and if it appears in a future post we will
    tell you.</span>
  </p>
  <form id="watch-form" name="watch" method="POST"
        data-netlify="true" netlify-honeypot="bot-field">
    <input type="hidden" name="form-name" value="watch">
    <p class="hp"><label>لا تملأ هذا الحقل <input name="bot-field"></label></p>

    <label for="w-id">رقم الشاسي أو اللوحة <span dir="ltr">/ Chassis or plate</span></label>
    <input type="text" id="w-id" name="identifier" required maxlength="40"
           placeholder="مثال: 1122334">

    <label for="w-contact">وسيلة تواصل — واتساب أو هاتف <span dir="ltr">/ WhatsApp or phone</span></label>
    <input type="tel" id="w-contact" name="contact" required maxlength="40"
           placeholder="09xxxxxxxx">

    <label for="w-note">وصف العربة (اختياري) <span dir="ltr">/ Vehicle description (optional)</span></label>
    <input type="text" id="w-note" name="note" maxlength="120"
           placeholder="كورولا بيضاء 2015">

    <label class="consent">
      <input type="checkbox" id="w-consent" required>
      <span>
        أوافق على حفظ هذه البيانات لغرض التنبيه فقط. لن تُنشر على هذه الصفحة
        ولن تُشارك مع أي طرف آخر. <strong>لا تتعامل مع أي شخص يطلب مالاً مقابل
        معلومات — الجهات الرسمية مجانية.</strong>
        <span dir="ltr">Stored only to alert you. Never published on this page,
        never shared. Never pay anyone for information — the official channels
        are free.</span>
      </span>
    </label>

    <button type="submit" id="w-submit">سجّل التنبيه <span dir="ltr">/ Register</span></button>
    <div class="watch-msg" id="watch-msg"></div>
  </form>
</div>

<div class="notice" id="notice-box">
  <strong>تنبيه مهم:</strong> هذه البيانات مجموعة آلياً من منشورات عامة على وسائل التواصل وقد تحتوي أخطاء.
  إذا وجدت عربتك: وثّق المنشور (لقطة شاشة + الرابط) وتواصل مع السلطات أو وسيط موثوق —
  <strong>لا تواجه البائع مباشرة</strong>، فذلك قد يكون خطراً وقد يجعل العربة تختفي مجدداً.
  <span dir="ltr">Data is auto-collected from public posts and may contain errors. If you find your
  vehicle: document the post and go through authorities — never confront a seller directly.</span>
  <br><br>
  السجلات المعلَّمة بـ <span class="chip warn">من صورة ⚠️</span> قُرئت آلياً من صورة وقد تحتوي
  أخطاء في الأرقام — راجع المنشور الأصلي قبل الاعتماد عليها.
  <span dir="ltr">Records marked "من صورة" were read from a photo by a vision model and may
  contain transcription errors — always check the original post.</span>
  <!-- Rendered statically so crawlers and link previews see the official
       channels; applyLang() rewrites it with the same helpHtml() on load. -->
  <div id="official-help"><br>__HELP_AR__</div>
  <br>آخر تحديث: <span id="upd">__UPDATED__</span></div>
<!-- Outside #notice-box on purpose: the language switch rewrites that box wholesale. -->
<div class="notice" id="site-links" style="text-align:center">
  <h2 class="vh">صفحات الموقع</h2>
  <a href="/guide.html">كيف تبحث عن عربتك المفقودة</a> ·
  <a href="/scams.html">احذر النصب</a> ·
  <a href="/yards.html">أحواش الحجز</a> ·
  <a href="/en.html">English</a>
  <br>
  <a href="/support.html">ادعم المشروع · Support the project</a> ·
  <a href="/stats.html">الإحصاءات · Statistics</a> ·
  <a href="/privacy.html">الخصوصية · Privacy</a>
  <br><br>
  <a class="fb" href="https://www.facebook.com/profile.php?id=61596074817604" target="_blank" rel="noopener">تابعنا على فيسبوك · Follow on Facebook</a>
  <button type="button" class="share" id="share-btn">شارك الموقع · Share</button>
</div>
</div>

__CLARITY__
<script type="module">
// The packed payload ships gzip-compressed and base64-encoded: at 355k rows
// the raw JSON passed 29MB, which is both over the artifact ceiling and
// hostile to readers on Sudanese mobile data. Decoded with the browser's
// native DecompressionStream (no external library, page stays one file).
// type="module" gives us top-level await; both features are ~2020-era.
// ---- analytics (Microsoft Clarity), only on the real site ----------------
// track() is a safe no-op when Clarity is absent (artifact copy, localhost,
// blocked by the visitor). Events: what was searched (type/results), what
// failed, what was clicked. Full session recordings and heatmaps come from
// the Clarity snippet itself.
const track = (name, tags) => {
  try {
    if (!window.clarity) return;
    clarity('event', name);
    if (tags) for (const [k, v] of Object.entries(tags)) clarity('set', k, String(v).slice(0, 100));
  } catch (e) {}
};
addEventListener('error', e => track('js_error', { error_msg: e.message || 'unknown' }));
addEventListener('unhandledrejection', e => track('js_rejection', { error_msg: (e.reason && e.reason.message) || 'unknown' }));

// ---- language: Arabic-first with an English option --------------------------
// Arabic is the page as authored; EN holds only the English strings and t()
// falls through to the Arabic literal, so removing a key can never blank the
// Arabic site. Cards/stats re-render on toggle; static text swaps in place.
let LANG = 'ar';
try { if (localStorage.getItem('lang') === 'en') LANG = 'en'; } catch (e) {}
const EN = {
  sub: 'A community registry of vehicles lost in the Sudan war — reports and sightings collected from public posts.',
  placeholder: 'e.g. 1122334 or 12345 kh1',
  hint: 'Search accepts any part of the chassis or plate number (Arabic or Latin digits), or the vehicle type/colour.',
  optModel: 'All types', optColor: 'All colours', optStatus: 'All statuses', optLocation: 'All locations',
  yearFrom: 'from year', yearTo: 'to year',
  lbChassis: 'has chassis number', lbContact: 'has contact number', lbLoose: 'show sightings with no number',
  reset: 'Clear filters',
  ask: 'Enter your chassis or plate number',
  askSub: '',
  hint: 'A part of the number is enough. Arabic or English digits both work. Or search by description: white Corolla 2015.',
  filtersSummary: 'Advanced search — make, colour, year, location',
  loading: 'Loading the registry…',
  st_spotted: 'spotted', st_for_sale: 'for sale', st_seeking_owner: 'seeking owner', st_recovered: 'recovered',
  r_ch: 'chassis match', r_pl: 'plate match', r_desc: 'description match', r_model: 'model match',
  r_year: 'year match',
  sightings: 'sightings', unknownVeh: 'Unidentified vehicle',
  conflictChip: '⚠️ make conflict',
  conflictTitle: 'The same number appears under different makes — possibly a chassis transplant (laundering). Check every post.',
  fromPhoto: 'from photo ⚠️',
  fromPhotoTitle: 'Read by machine from a photo — verify against the original post',
  uncertain: 'disputed number ⚠️',
  uncertainTitle: 'Two independent readers disagreed on some characters — do not rely on this number without checking the original image',
  chassisLbl: 'Chassis', plateLbl: 'Plate', contactLbl: 'Contact', openPost: 'Original post ↗',
  noLink: 'no saved link', searchGroup: 'Search this number in the group ↗',
  tlWait: 'Loading sightings…', tlFailed: 'Could not load the sightings — reload the page to try again.',
  detailWait: 'Searching by description and the filters needs the rest of the registry — it is loading and the results will appear by themselves. Searching by chassis or plate number works now.',
  detailFailedWait: 'Searching by description and the filters needs the rest of the registry, which could not be loaded — reload the page. Searching by chassis or plate number works now.',
  showAll: 'Show all sightings',
  contactWarn: '⚠️ Before calling: never pay money up front and never share your chassis number in private messages — official recovery is free. Details at the bottom of the page.',
  covAnalyzed: 'posts analysed', covVehicles: 'vehicles in the registry', covRecords: 'recorded sightings',
  covSources: 'sources monitored', covPhotos: 'photos archived',
  src_telegram: 'Telegram', src_facebook: 'Facebook', src_intake: 'direct reports',
  emptyFilters: 'No vehicles match these filters.',
  emptyId: 'We did not find this number in the registry — that does not mean the vehicle is gone, only that it has not been sighted in any public post yet.',
  emptyText: 'No results for this search. Try the chassis or plate number — searching by number is far more accurate than by description.',
  showMore: 'Show more', chHasChassis: 'has chassis', chHasContact: 'has contact',
  chLoose: 'includes number-less sightings', chYear: 'year',
};
EN.sortTexts = ['Best match', 'Newest sighting first', 'Oldest sighting first', 'Most sighted'];
EN.helpHtml = `<div class="help">
  <strong>Official free channels — they work even if your car is not listed here:</strong>
  <ol>
    <li><strong>File a free report:</strong>
      <a href="https://sudanpolice.net/reg/car_stealing" target="_blank" rel="noopener">sudanpolice.net/reg/car_stealing</a>
      — the report places a <strong>legal ban</strong> on re-licensing the vehicle and lists it
      on the INTERPOL platform, so nobody can re-register it.</li>
    <li><strong>Go in person:</strong> the Traffic Department, or the vehicle-theft
      branch of the investigations department — ask there about the impound yards and
      collected vehicles. <strong>There is no official WhatsApp number.</strong> Any
      account presenting one is not the police.</li>
    <li><strong>Official yards are searched by chassis number at the gate:</strong>
      Traffic yard — Al-Arbaeen St., Omdurman · Traffic yard — Khartoum Bahri ·
      the Khartoum State collection committee yards. Bring ID and ownership documents.</li>
    <li>The registry updates continuously — <strong>come back and search again later.</strong></li>
  </ol>
  <div class="danger-box"><strong>Beware of scams:</strong> official bodies do not operate
  through personal WhatsApp accounts and never charge a fee to "release" your car. Do not send
  your chassis number in private messages to any "search platform", do not pay money up front,
  and never read your 6-digit verification code to anyone. Official recovery is free.</div>
</div>`;
EN.notice = (updated) => `<strong>Important:</strong> this data is collected automatically from public
  social-media posts and may contain errors. If you find your vehicle: document the post
  (screenshot + link) and go through the authorities or a trusted intermediary —
  <strong>never confront a seller directly</strong>; it can be dangerous and can make the car disappear again.
  <br><br>
  Records marked <span class="chip warn">from photo ⚠️</span> were machine-read from an image and may
  contain digit errors — always check the original post before relying on them.
  <div id="official-help"></div>
  <br>Last updated: <span id="upd">${updated}</span>`;
const t = (key, ar) => (LANG === 'en' ? (EN[key] ?? ar) : ar);

const ALIASES = __ALIASES__;
const STATS = __STATS__;
const FBKIND = __FBKIND__;   // facebook source id -> "g" group | "p" profile/page
const SRCNAME = __SRCNAMES__; // facebook source id -> group name (config.yaml)

const AR_DIGITS = {'٠':'0','١':'1','٢':'2','٣':'3','٤':'4','٥':'5','٦':'6','٧':'7','٨':'8','٩':'9',
                   '۰':'0','۱':'1','۲':'2','۳':'3','۴':'4','۵':'5','۶':'6','۷':'7','۸':'8','۹':'9'};
const trAr = s => (s || '').replace(/[٠-٩۰-۹]/g, d => AR_DIGITS[d]);
const digitsOnly = s => trAr(s).replace(/\D/g, '');
const normChassis = s => trAr(s).toUpperCase().replace(/[^A-Z0-9]/g, '');
// Mirrors normalize.is_plate_shaped — change one, change both. A region marker
// is one Arabic letter (not و), or two only with a digit attached.
const PLATE_WORD = /^(?:ال)?لوح[ةه]$/;
const MARKER_TOKEN = /^(?:[ء-نهي][0-9]?|[0-9][ء-نهي]|[ء-ي]{2}[0-9]|[0-9][ء-ي]{2})$/;
const GLUED_TOKEN = /^(?:[ء-ي]{1,2}[0-9]{4,}|[0-9]{3,}[ء-ي]{1,2}[0-9]?)$/;
function isPlateShaped(s) {
  const v = trAr(s);
  if (!/[0-9]{3,}/.test(v)) return false;
  return v.split(/[\s/\\_,،.:|-]+/).some(tk =>
    PLATE_WORD.test(tk) || MARKER_TOKEN.test(tk) || GLUED_TOKEN.test(tk));
}

// Arabic normalization + consonant skeleton, mirroring carhunt/aliases.py:
// كورولا / كورلا / كرلا / كريلا all reduce to كرل and match Toyota Corolla.
const normAr = s => (s || '').toLowerCase()
  .replace(/[أإآ]/g, 'ا').replace(/ة/g, 'ه').replace(/ى/g, 'ي')
  .replace(/ؤ/g, 'و').replace(/ئ/g, 'ي').replace(/[ـً-ٰٟ]/g, '');
const skeleton = w => { w = normAr(w); return w ? w[0] + w.slice(1).replace(/[اوي]/g, '') : ''; };

const ALIAS_EXACT = {}, ALIAS_SKEL = {};
for (const [key, names] of Object.entries(ALIASES)) {
  for (const name of [...names, key]) {
    ALIAS_EXACT[normAr(name)] = key;
    const s = skeleton(name);
    if (s.length >= 3 && !(s in ALIAS_SKEL)) ALIAS_SKEL[s] = key;
  }
}
function modelKeysIn(text) {
  const n = normAr(text), found = new Set();
  for (const [alias, key] of Object.entries(ALIAS_EXACT)) if (n.includes(alias)) found.add(key);
  for (const w of n.match(/[؀-ۿ]{3,}/g) || []) {
    const key = ALIAS_SKEL[skeleton(w)];
    if (key) found.add(key);
  }
  return found;
}

function chassisOverlap(query, chassis) {
  const q = normChassis(query), c = normChassis(chassis);
  if (q.length < 4 || c.length < 4) return 0;
  const [shorter, longer] = q.length <= c.length ? [q, c] : [c, q];
  return (longer.endsWith(shorter) || longer.includes(shorter)) ? shorter.length : 0;
}

const TEXT_FIELDS = ['make','model','color','year','status','location','description'];
function score(query, v) {
  let s = 0; const reasons = [];
  const qd = digitsOnly(query);
  if (qd.length >= 4) {
    const ov = chassisOverlap(query, v.chassis || '');
    if (ov) { s += 40 + ov * 8; reasons.push(t('r_ch', 'تطابق شاسي')); }
    // match any digit run of the plate: "12345 خ1" must be found by "12345"
    const plateTxt = trAr(v.plate || '');
    const plateRuns = plateTxt.match(/[0-9]{3,}/g) || [];
    // when the region marker is glued on with no separator ("خ١١٢٣٤٥") the
    // whole plate is ONE run whose leading digit is the region, not the
    // serial — so the serial the owner remembers would never match. Mirror
    // normalize.norm_plate and offer the peeled serial as a candidate too.
    const glued = plateTxt.match(/[ء-ي]\s*[0-9]([0-9]{5,})/);
    if (glued) plateRuns.push(glued[1]);
    for (const run of plateRuns) {
      if (qd === run) { s += 60; reasons.push(t('r_pl', 'تطابق لوحة')); break; }
    }
  }
  const terms = trAr(query).split(/[\s،]+/).filter(t => t.length >= 2 && !/^\d+$/.test(t));
  if (terms.length) {
    const hay = normAr(TEXT_FIELDS.map(f => String(v[f] || '')).join(' '));
    const hits = terms.filter(t => hay.includes(normAr(t)));
    if (hits.length) { s += 10 * hits.length; reasons.push('تطابق وصف'); }
  }
  const qKeys = modelKeysIn(query);
  if (qKeys.size && v.model_key && qKeys.has(v.model_key)) {
    s += 25; reasons.push('تطابق موديل');
  }
  return { s, reasons };
}

const STATUS_AR = { spotted: 'شوهدت', for_sale: 'معروضة للبيع', seeking_owner: 'تبحث عن صاحبها',
                    recovered: 'مستردة', unknown: '' };
const esc = x => String(x).replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

// ---- load the prebuilt registry ------------------------------------------
// Dossiers, their search index and the dropdown tallies are all computed at
// BUILD time (see export_site.py). The browser used to redo that work on every
// visit — 355k objects materialised, then ~83k dossiers grouped, sorted and
// string-built — which blocked the main thread for seconds and made every tap
// wait (Clarity measured INP 2.8s, "poor", on an 82% mobile audience).
//
// A dossier groups the many posts about ONE physical car: the same recovery
// list gets reposted between groups, sellers relist, police inventories
// overlap. Grouping is by EXACT normalised identifier only — suffix-merging
// could fuse two different cars sharing a 6-digit tail, and a wrong merge is
// worse than showing both (suffix-tolerant SEARCH surfaces them anyway).
// The payload is fetched over Sudanese and Gulf mobile data, so a dropped
// download is a NORMAL event, not an exceptional one: Clarity recorded
// `js_error: failed to fetch` plus a `decode_failure` in the field. Each file
// is retried before giving up, and the give-up state says to reload rather
// than sitting silent. Versioned per build (content hash) so `force-cache`
// cannot pin a returning visitor to a stale payload. See export_site.py.
//
// TWO files since 2026-10-13. core.bin (~1.7MB) answers a number search, the
// thing nearly every visitor came to do; detail.bin (~8MB) brings the sighting
// timelines, text search and the dropdown filters, and loads right after.
// Before the split, one 9.8MB file unpacking to 58MB of JSON had to land
// before ANYTHING worked, and Clarity recorded a visitor in Sudan tapping a
// disabled box for a whole minute and leaving. While core loads, the box still
// takes typing (echoed back in the status line), the status line counts the
// download, and the language button works.
const CORE_URL = '__CORE_URL__';
const DETAIL_URL = '__DETAIL_URL__';
let READY = false, GAVE_UP = false;
const $q0 = document.getElementById('q');
// ?q=<number> pre-fills the box: the schema.org SearchAction target, and what a
// link from the Facebook Page or a chat message carries. render() reads the box
// the moment the data lands, so nothing else is needed.
try { const qp = new URLSearchParams(location.search).get('q'); if (qp && !$q0.value) $q0.value = qp.trim().slice(0, 80); } catch (e) {}
const $st0 = document.getElementById('stats');
let dlGot = 0, dlTotal = 0, dlParsing = false;
const pct = (got, total) => got
  ? (total ? ` ${Math.min(99, Math.floor(got * 100 / total))}%` : ` ${(got / 1048576).toFixed(1)} MB`)
  : '';
function showLoading() {
  if (READY || GAVE_UP) return;
  const en = LANG === 'en';
  let line = dlParsing ? (en ? 'Preparing the registry…' : 'جارٍ تجهيز السجل…')
                       : (en ? 'Loading the registry…' : 'جاري تحميل السجل…') + pct(dlGot, dlTotal);
  const q = $q0.value.trim();
  if (q) line += en ? ` — your search for “${q}” runs as soon as it is ready.`
                    : ` — سيظهر البحث عن «${q}» فور اكتمال التحميل.`;
  $st0.textContent = line;
}
$q0.addEventListener('input', showLoading);
document.getElementById('lang-toggle').addEventListener('click', () => {
  LANG = LANG === 'en' ? 'ar' : 'en';
  if (READY) applyLang();
  else {
    // The full switch re-renders cards and tallies, which need the data; flip
    // what exists now and let the applyLang() at the end finish the job.
    const en = LANG === 'en', w = document.querySelector('.wrap');
    w.dir = en ? 'ltr' : 'rtl';
    w.lang = en ? 'en' : 'ar';
    document.documentElement.dir = w.dir; document.documentElement.lang = w.lang;
    document.getElementById('lang-toggle').textContent = en ? 'عربي' : 'English';
    try { localStorage.setItem('lang', LANG); } catch (e) {}
    showLoading();
  }
  track('lang_switch', { lang: LANG });
});

// Fetch, count, unpack and parse one payload, with retries and backoff.
// progress(got, total, parsing) is called as bytes arrive; onRetry(attempt)
// before each retry.
async function fetchPayload(url, progress, onRetry) {
  const name = url.split('?')[0];
  let lastErr;
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const resp = await fetch(url, { cache: 'force-cache' });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      const total = Number(resp.headers.get('content-length')) || 0;
      let got = 0, lastPaint = 0;
      progress(0, total, false);
      const counted = resp.body.pipeThrough(new TransformStream({
        transform(chunk, ctl) {
          got += chunk.byteLength;
          const now = performance.now();
          if (now - lastPaint > 250) { lastPaint = now; progress(got, total, false); }
          ctl.enqueue(chunk);
        },
      }));
      const text = await new Response(counted.pipeThrough(new DecompressionStream('gzip'))).text();
      // JSON.parse blocks the main thread for seconds on a phone: say so first,
      // and give the browser one frame to paint it. NOT rAF alone: a background
      // tab never runs animation frames, so a page opened in a new tab sat at
      // "preparing" until someone looked at it. The timer is the fallback.
      progress(got, total, true);
      await new Promise(r => { requestAnimationFrame(() => setTimeout(r, 0)); setTimeout(r, 60); });
      return JSON.parse(text);
    } catch (e) {
      lastErr = e;
      track('payload_retry', { payload: name, attempt });
      if (attempt < 3) {
        if (onRetry) onRetry(attempt + 1);
        await new Promise(r => setTimeout(r, 1500 * attempt));   // backoff
      }
    }
  }
  track('decode_failure', { payload: name });
  throw lastErr;
}

let P;
try {
  P = await fetchPayload(CORE_URL,
    (got, total, parsing) => { dlGot = got; dlTotal = total; dlParsing = parsing; showLoading(); },
    attempt => {
      document.getElementById('results').innerHTML =
        '<div class="empty">جارٍ تحميل قاعدة البيانات… (محاولة ' + attempt + ' من 3)' +
        '<br><span dir="ltr">Loading the database… (attempt ' + attempt + ' of 3)</span></div>';
    });
} catch (e) {
  GAVE_UP = true;   // stop the "loading…" line overwriting the give-up message below
  document.getElementById('stats').textContent = '';
  document.getElementById('results').innerHTML =
    '<div class="empty">تعذّر تحميل قاعدة البيانات بعد ٣ محاولات. تأكد من اتصالك وحدّث الصفحة.' +
    '<br><span dir="ltr">Could not load the database after 3 attempts. ' +
    'Check your connection and reload.</span></div>';
  throw e;
}
// detail.bin fills these in (loadDetail, at the end). Until then every reader
// of sightings, tables or the text index must check DETAIL first.
P.sg = null;
P.t = {};
let DETAIL = false, DETAIL_FAILED = false, awaitingDetail = false;

// Core is in. The box has been taking input all along; the applyLang() at
// the end runs the first render(), which reads whatever was typed while
// loading. (The placeholder is no longer swapped here: ORIG captures it below,
// and swapping it to English before that capture left an English placeholder
// on the Arabic page after toggling back.)

const N = P.hd.length;
const IX = P.ix;
const KPOS = {};
P.k.forEach((k, i) => { KPOS[k] = i; });

// Only the dossiers actually on screen become objects.
function dossierAt(i) {
  const h = P.hd[i], keys = P.k, tables = P.t;
  const sightings = !DETAIL ? null : P.sg[i].map(row => {
    const rec = {};
    for (let j = 0; j < keys.length && j < row.length; j++) {
      const v = row[j];
      if (v === 0) continue;
      const tb = tables[keys[j]];
      rec[keys[j]] = tb ? tb[v] : v;
    }
    return rec;
  });
  return {
    color: h[0] || null, make: h[1] || null, model: h[2] || null, year: h[3] || null,
    chassis: h[4] || null, plate: h[5] || null,
    conflict: !!(h[6] & 1), ph: !!(h[6] & 2), lc: !!(h[6] & 4),
    contacts: h[7], count: h[8] || (sightings ? sightings.length : 0), sightings,
  };
}

// Browse order ships precomputed (newest sighting first), so the default view
// never sorts 83k entries. The other orders are derived once, on first use.
const ORDERS = { latest: P.ord };
function orderBy(mode) {
  if (!ORDERS[mode]) {
    const idx = P.ord.slice();
    if (mode === 'oldest') {
      idx.sort((a, b) => { const x = IX.old[a] || '9999', y = IX.old[b] || '9999'; return (x > y) - (x < y); });
    } else if (mode === 'most') {
      idx.sort((a, b) => (P.hd[b][8] || 0) - (P.hd[a][8] || 0));   // [8] = report count, in core.bin
    }
    ORDERS[mode] = idx;
  }
  return ORDERS[mode];
}

// ---- coverage: how much has been scanned, and from where ----
const nf0 = () => new Intl.NumberFormat(LANG === 'en' ? 'en-US' : 'ar-EG');
let nf = nf0();
const SOURCE_LABEL = { 'telegram': 'تلجرام', 'facebook': 'فيسبوك', 'intake': 'بلاغات مباشرة' };
function prettySource(name) {
  const [kind, rest] = String(name).split(':');
  const label = t('src_' + kind, SOURCE_LABEL[kind] || kind);
  const nice = kind === 'facebook' ? (SRCNAME[rest] || rest) : rest;
  return nice ? `${label} · ${nice}` : label;
}
// Where a source lives. Facebook ids are groups unless the saved links say
// profile (FBKIND); Telegram names are public channels; direct reports have no
// page to open.
function sourceUrl(name) {
  const [kind, rest] = String(name).split(':');
  if (!rest) return null;
  if (kind === 'telegram') return `https://t.me/${rest}`;
  if (kind === 'facebook') return FBKIND[rest] === 'p'
    ? `https://www.facebook.com/profile.php?id=${rest}`
    : `https://www.facebook.com/groups/${rest}`;
  return null;
}
function renderCoverage() {
  document.getElementById('coverage').innerHTML = [
    [t('covAnalyzed', 'منشور تم فحصه'), STATS.analyzed],
    [t('covVehicles', 'عربة في السجل'), P.nid],
    [t('covRecords', 'رصد مسجَّل'), STATS.vehicles],
    [t('covSources', 'مصدر يُراقب'), STATS.sources.length],
    [t('covPhotos', 'صورة محفوظة'), STATS.photos],
  ].map(([lbl, n]) =>
    `<div class="stat"><div class="n">${nf.format(n)}</div><div class="lbl">${lbl}</div></div>`
  ).join('');
  document.getElementById('sources-summary').textContent = LANG === 'en'
    ? `Monitored sources (${nf.format(STATS.sources.length)}) — ${nf.format(STATS.posts)} posts collected`
    : `المصادر التي يتم فحصها (${nf.format(STATS.sources.length)}) — إجمالي ${nf.format(STATS.posts)} منشور مجموع`;
  document.getElementById('sources-list').innerHTML = STATS.sources.map(x => {
    const url = sourceUrl(x.name), lbl = esc(prettySource(x.name));
    const name = url
      ? `<a href="${url}" target="_blank" rel="noopener">${lbl} ↗</a>`
      : `<b>${lbl}</b>`;
    return `<li>${name}<span>${nf.format(x.posts)}</span></li>`;
  }).join('');
}

// ---- official free channels + scam warning (empty state AND footer) ----
const helpHtml = () => LANG === 'en' ? EN.helpHtml : `__HELP_AR__`;

// ---- one dossier card: identity header + sighting timeline ----
// A row stands for every copy of the same line (export_site merges reposts);
// `n` is how many copies, `f` the first date. The chip explains itself on tap.
function repostChip(x) {
  const n = Number(x.n), en = LANG === 'en';
  const lbl = en ? `posted ${nf.format(n)}×`
    : (n === 2 ? 'نُشر مرتين' : `نُشر ${nf.format(n)} ${n <= 10 ? 'مرات' : 'مرة'}`);
  const first = x.f ? (en ? ` First seen ${x.f}.` : ` أول ظهور: ${x.f}.`) : '';
  const why = en
    ? `The same line from a list that was reposted in several groups (copies: ${nf.format(n)}) — shown once here.${first}`
    : `نفس السطر من قائمة أُعيد نشرها في عدة مجموعات (عدد النسخ: ${nf.format(n)}) — يُعرض هنا مرة واحدة.${first}`;
  return `<span class="chip" title="${esc(why)}">${lbl}</span>`;
}
function fbSearchUrl(x) {
  const src = x.source || '';
  if (!src.startsWith('facebook:')) return '';
  const q = normChassis(x.chassis || '') || digitsOnly(x.plate || '');
  if (q.length < 4) return '';
  const id = src.slice(9);
  return FBKIND[id] === 'p'
    ? `https://www.facebook.com/profile/${id}/search/?q=${encodeURIComponent(q)}`
    : `https://www.facebook.com/groups/${id}/search/?q=${encodeURIComponent(q)}`;
}
function tlEntry(x) {
  const bits = [];
  if (x.status && STATUS_AR[x.status]) bits.push(`<span class="chip">${t('st_' + x.status, STATUS_AR[x.status])}</span>`);
  if (x.ph) bits.push(`<span class="chip warn" title="${t('fromPhotoTitle', 'قُرئت من صورة — تحقق من المنشور الأصلي')}">${t('fromPhoto', 'من صورة ⚠️')}</span>`);
  if (x.lc) bits.push(`<span class="chip warn" title="${t('uncertainTitle', 'قارئان مستقلان اختلفا على بعض الحروف — لا تعتمد على هذا الرقم دون فحص الصورة الأصلية')}">${t('uncertain', 'رقم غير مؤكد ⚠️')}</span>`);
  if (x.n > 1) bits.push(repostChip(x));
  if (x.location) bits.push(`<span>${esc(x.location)}</span>`);
  if (x.contact) bits.push(`<span>${t('contactLbl', 'للتواصل')} <bdi dir="ltr">${esc(x.contact)}</bdi></span>`);
  // People tapped linkless rows over and over looking for the post (older
  // Facebook rows predate the 2026-10-08 link fix). With no saved link, a
  // Facebook row offers the next best thing: the row's number searched inside
  // the same group or profile. Labelled as a search, never as the post.
  const search = x.url ? '' : fbSearchUrl(x);
  const href = x.url || search;
  const link = x.url
    ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">${t('openPost', 'المنشور الأصلي ↗')}</a>`
    : search
      ? `<a href="${esc(search)}" target="_blank" rel="noopener">${t('searchGroup', 'ابحث عن الرقم في المجموعة ↗')}</a>`
      : `<span class="nolink">${t('noLink', 'لا يوجد رابط محفوظ')}</span>`;
  return `<li class="tl-item"${href ? ` data-url="${esc(href)}"` : ''}>
    <span class="tl-date"><bdi dir="ltr">${esc((x.seen_at || '').slice(0, 10) || '—')}</bdi></span>
    <div class="tl-body">
      <div class="tl-top">${bits.join(' ')}</div>
      ${x.description ? `<div class="desc">${esc(x.description)}</div>` : ''}
      <div class="tl-src">${link}<span class="muted">${esc(prettySource(x.source || ''))}</span></div>
    </div></li>`;
}
const TL_SHOW = 4;
function dossierCard(d, reasons) {
  const veh = [d.color, d.make, d.model, d.year].filter(Boolean).join(' ') || t('unknownVeh', 'عربة غير محددة');
  const chips = [];
  if (reasons && reasons.length) chips.push(`<span class="chip strong">${reasons.join(' + ')}</span>`);
  if (d.count > 1) chips.push(`<span class="chip count">${nf.format(d.count)} ${t('sightings', 'رصدات')}</span>`);
  if (d.conflict) chips.push(`<span class="chip danger" title="${t('conflictTitle', 'نفس الرقم ظهر بأنواع عربات مختلفة — قد يعني نقل شاسي (غسيل عربة). افحص كل المنشورات')}">${t('conflictChip', '⚠️ تضارب في النوع')}</span>`);
  const ids = [
    d.chassis ? `<div>${t('chassisLbl', 'شاسي')} <bdi dir="ltr">${esc(d.chassis)}</bdi></div>` : '',
    d.plate ? `<div>${t('plateLbl', 'لوحة')} <bdi dir="ltr">${esc(d.plate)}</bdi></div>` : '',
  ].join('');
  if (!d.sightings) {
    // core.bin is in, detail.bin is not: the header is real, the timeline follows
    const wait = DETAIL_FAILED
      ? t('tlFailed', 'تعذّر تحميل الرصدات — حدّث الصفحة لإعادة المحاولة.')
      : t('tlWait', 'جارٍ تحميل الرصدات…');
    return `<div class="card">
    <div class="top"><span class="veh">${esc(veh)}</span>${chips.join('')}</div>
    <div class="idrow">${ids}</div>
    <div class="tl-wait">${wait}</div>
  </div>`;
  }
  const head = d.sightings.slice(0, TL_SHOW).map(tlEntry).join('');
  const rest = d.sightings.length > TL_SHOW
    ? `<details class="tl-more"><summary>${t('showAll', 'عرض كل الرصدات')} (${nf.format(d.sightings.length)})</summary>
       <ul class="tl">${d.sightings.slice(TL_SHOW).map(tlEntry).join('')}</ul></details>`
    : '';
  const warn = d.contacts
    ? `<div class="contact-warn">${t('contactWarn', '⚠️ قبل الاتصال: لا ترسل مالاً مقدماً ولا رقم شاسيك في الخاص — الاسترداد الرسمي مجاني، والتفاصيل أسفل الصفحة.')}</div>`
    : '';
  return `<div class="card">
    <div class="top"><span class="veh">${esc(veh)}</span>${chips.join('')}</div>
    <div class="idrow">${ids}</div>
    <ul class="tl">${head}</ul>${rest}${warn}
  </div>`;
}

// ---- controls ----
const $q = document.getElementById('q');
const $res = document.getElementById('results');
const $stats = document.getElementById('stats');
const $chips = document.getElementById('chipbar');
const $more = document.getElementById('more');
const $shownLbl = document.getElementById('shown');
const F = {
  model: document.getElementById('f-model'),
  color: document.getElementById('f-color'),
  status: document.getElementById('f-status'),
  location: document.getElementById('f-location'),
  yearMin: document.getElementById('f-year-min'),
  yearMax: document.getElementById('f-year-max'),
  chassis: document.getElementById('f-chassis'),
  contact: document.getElementById('f-contact'),
  sort: document.getElementById('f-sort'),
  loose: document.getElementById('f-loose'),
};

const MODEL_AR = {};
for (const [key, names] of Object.entries(ALIASES)) {
  const ar = names.find(n => /[؀-ۿ]/.test(n));
  MODEL_AR[key] = ar ? `${ar} · ${key}` : key;
}
function fill(sel, entries, labeller) {
  for (const [val, n] of entries) {
    const o = document.createElement('option');
    o.value = val;
    o.textContent = `${labeller ? labeller(val) : val} (${n})`;
    sel.appendChild(o);
  }
}
const statusTally = new Map(P.tal.status);
fill(F.model, P.tal.model_key, k => MODEL_AR[k] || k);
fill(F.color, P.tal.color);
fill(F.status, P.tal.status, x => STATUS_AR[x] || x);
fill(F.location, P.tal.location);

// ---- filters ----
// Filter values resolve to their PACKED representation once per render, so the
// per-sighting test is an integer compare instead of rebuilding objects. A
// dossier passes when any ONE of its sightings satisfies every active filter.
function packedWant(field, value) {
  const tb = P.t[field];
  if (!tb) return value;
  const at = tb.indexOf(value);
  return at > 0 ? at : -1;
}
function filterSpec() {
  const eq = [];
  const add = (field, el) => { if (el.value) eq.push([KPOS[field], packedWant(field, el.value)]); };
  add('model_key', F.model); add('color', F.color);
  add('status', F.status); add('location', F.location);
  const a = parseInt(F.yearMin.value, 10), b = parseInt(F.yearMax.value, 10);
  return {
    eq,
    yMin: Number.isNaN(a) ? null : a,
    yMax: Number.isNaN(b) ? null : b,
    needChassis: F.chassis.checked,
    needContact: F.contact.checked,
    loose: F.loose.checked,
  };
}
function rowPasses(row, W) {
  for (let k = 0; k < W.eq.length; k++) {
    const pos = W.eq[k][0];
    if ((pos < row.length ? row[pos] : 0) !== W.eq[k][1]) return false;
  }
  if (W.yMin !== null || W.yMax !== null) {
    const raw = KPOS.year < row.length ? row[KPOS.year] : 0;
    if (!raw) return false;
    const tb = P.t.year;
    const m = String(tb ? tb[raw] : raw).match(/(19|20)\d{2}/);
    if (!m) return false;
    const y = parseInt(m[0], 10);
    if (W.yMin !== null && y < W.yMin) return false;
    if (W.yMax !== null && y > W.yMax) return false;
  }
  return true;
}
function dossierPasses(i, W) {
  if (!W.loose && !IX.cn[i] && !IX.pr[i]) return false;
  if (W.needChassis && !IX.cn[i]) return false;
  if (W.needContact && !P.hd[i][7]) return false;
  if (!W.eq.length && W.yMin === null && W.yMax === null) return true;
  const rows = P.sg[i];
  for (let r = 0; r < rows.length; r++) if (rowPasses(rows[r], W)) return true;
  return false;
}
function activeChips() {
  const out = [];
  if (F.model.value) out.push(MODEL_AR[F.model.value] || F.model.value);
  if (F.color.value) out.push(F.color.value);
  if (F.status.value) out.push(t('st_' + F.status.value, STATUS_AR[F.status.value] || F.status.value));
  if (F.location.value) out.push(F.location.value);
  if (F.yearMin.value || F.yearMax.value)
    out.push(`${t('chYear', 'سنة')} ${F.yearMin.value || '…'}–${F.yearMax.value || '…'}`);
  if (F.chassis.checked) out.push(t('chHasChassis', 'لها شاسي'));
  if (F.contact.checked) out.push(t('chHasContact', 'لها تواصل'));
  if (F.loose.checked) out.push(t('chLoose', 'تشمل رصدات بلا رقم'));
  return out;
}

// ---- query parsing and scoring ----
function queryCtx(query) {
  const raw = trAr(query);
  const tokens = raw.split(/[\s،]+/).filter(Boolean);
  // A standalone 19xx/20xx token is a MODEL YEAR, not identifier material.
  // Without this, "كورولا 2015" hunts for a chassis containing 2015 and either
  // returns nothing or returns junk that merely ends in those digits.
  const years = tokens.filter(x => /^(19|20)\d{2}$/.test(x));
  const idSrc = tokens.filter(x => !years.includes(x)).join(' ');
  // A plate typed with its region marker ("41019 خ 9") has NO chassis reading:
  // normChassis glues it into "410199", a number written nowhere, which then
  // "matches" any chassis ending in it. Mirrors normalize.is_plate_shaped.
  const plate = isPlateShaped(idSrc);
  return {
    qd: digitsOnly(idSrc),
    qn: plate ? '' : normChassis(idSrc),
    plate,
    // every digit run separately: a plate typed as "12345 خ1" must match the
    // stored run "12345". Concatenating gave "123451", which matches nothing.
    runs: (idSrc.match(/[0-9]{3,}/g) || []),
    years,
    terms: tokens.filter(x => x.length >= 2 && !/^\d+$/.test(x)).map(normAr),
    qKeys: modelKeysIn(query),
  };
}
function dscoreAt(i, ctx) {
  let sc = 0; const reasons = [];
  if (ctx.qd.length >= 4) {
    const cn = IX.cn[i];
    if (cn.length >= 4 && ctx.qn.length >= 4) {
      // direction matters: a SHORT QUERY was typed deliberately (>=4 ok),
      // but a short STORED fragment inside a longer query is usually noise —
      // demand >=5 as a suffix, >=6 mid-string, or chassis 4209 "matches"
      // any long number containing those four digits.
      const qShorter = ctx.qn.length <= cn.length;
      const sh = qShorter ? ctx.qn : cn, lo = qShorter ? cn : ctx.qn;
      const okEnd = lo.endsWith(sh) && sh.length >= (qShorter ? 4 : 5);
      const okIn = lo.includes(sh) && sh.length >= (qShorter ? 4 : 6);
      if (okEnd || okIn) { sc += 40 + sh.length * 8; reasons.push(t('r_ch', 'تطابق شاسي')); }
    }
    if (!reasons.length && ctx.runs.length) {
      const pr = IX.pr[i];
      if (pr) for (let k = 0; k < ctx.runs.length; k++) {
        if (pr.indexOf(',' + ctx.runs[k] + ',') !== -1) { sc += 60; reasons.push(t('r_pl', 'تطابق لوحة')); break; }
      }
      // a plate written alone on a list line ("41019 / خ #") is stored as that
      // row's chassis, so a plate query must find its serial there — exactly
      if (!reasons.length && ctx.plate) {
        const cn = IX.cn[i];
        if (cn.length >= 4 && ctx.runs.indexOf(cn) !== -1) { sc += 60; reasons.push(t('r_pl', 'تطابق لوحة')); }
      }
    }
    // A pure number that matches nothing IS zero results — that is the design:
    // never answer an identifier search with a resemblance. But when the person
    // also typed words ("كورولا 2015"), those words are the real query and must
    // still be scored. Bailing here produced a 20-of-21 zero-result rate.
    if (!sc && !ctx.terms.length && !ctx.qKeys.size) return null;
  }
  if (ctx.terms.length) {
    const hay = IX.hay[i];
    let hits = 0;
    for (let k = 0; k < ctx.terms.length; k++) if (hay.indexOf(ctx.terms[k]) !== -1) hits++;
    if (hits) { sc += 10 * hits; reasons.push(t('r_desc', 'تطابق وصف')); }
  }
  if (ctx.qKeys.size && IX.mk[i] && ctx.qKeys.has(IX.mk[i])) { sc += 25; reasons.push(t('r_model', 'تطابق موديل')); }
  if (ctx.years.length && IX.yr[i] && ctx.years.indexOf(String(IX.yr[i])) !== -1) {
    sc += 20; reasons.push(t('r_year', 'تطابق سنة'));
  }
  return sc > 0 ? { s: sc, reasons } : null;
}

// ---- results ----
const PAGE = 30;
const SCORE = new Float64Array(N);
let shown = PAGE;
let list = [];       // dossier indices, already in display order
let curCtx = null;   // active query context, for re-deriving the visible chips

// Scanning 83k dossiers in one synchronous loop is what pinned INP at 2.8-3.0s
// ("poor") in the field on a 64%-mobile audience — Clarity measured it BEFORE
// and AFTER the build-time index rewrite and the number did not move, because
// that rewrite removed the per-visit GROUPING work but left this per-keystroke
// SCAN on the main thread. A tap arriving mid-scan waits for the whole thing.
//
// So the scan yields to the event loop every CHUNK entries. Total work is
// unchanged and slightly higher; what changes is that the browser can paint and
// respond to input between chunks, which is exactly what INP measures.
// MUST be a power of two: the loops test `(k & (CHUNK - 1)) === CHUNK - 1`,
// which is only a "every CHUNK iterations" check when the mask is all ones.
// 4000 silently yields on an irregular subset of iterations instead.
const CHUNK = 4096;

// NOT setTimeout(0). Browsers clamp nested timers to ~4ms and throttle them to
// ~1/second in a BACKGROUND tab, so a 20-chunk scan took ~20 seconds and the
// page rendered nothing at all — measured, this exact bug, before this comment
// existed. `scheduler.yield()` is the purpose-built primitive; the
// MessageChannel fallback posts a task, which no clamp applies to.
const yieldToUI = (typeof scheduler !== 'undefined' && scheduler.yield)
  ? () => scheduler.yield()
  : (() => {
      const mc = new MessageChannel();
      const waiting = [];
      mc.port1.onmessage = () => { const f = waiting.shift(); if (f) f(); };
      return () => new Promise(r => { waiting.push(r); mc.port2.postMessage(0); });
    })();

// A newer render supersedes an older one. Without this, fast typing stacks
// several full scans that all finish and fight over the DOM, and the last
// paint can be from a STALE query — worse than being slow, it is wrong.
let renderGen = 0;

function paint() {
  const slice = list.slice(0, shown);
  $res.innerHTML = slice.map(i => {
    const r = curCtx ? dscoreAt(i, curCtx) : null;
    return dossierCard(dossierAt(i), r ? r.reasons : null);
  }).join('');
  const total = list.length;
  const hasMore = total > shown;
  $more.hidden = !hasMore;
  $more.textContent = hasMore ? `${t('showMore', 'عرض المزيد')} (${nf.format(Math.min(PAGE, total - shown))})` : '';
  $shownLbl.textContent = total
    ? (LANG === 'en' ? `Showing ${nf.format(Math.min(shown, total))} of ${nf.format(total)}`
                     : `عرض ${nf.format(Math.min(shown, total))} من ${nf.format(total)}`)
    : '';
}

async function render() {
  const gen = ++renderGen;
  const query = $q.value.trim();
  const chips = activeChips();
  $chips.innerHTML = chips.map(c => `<span class="f">${esc(c)}</span>`).join('');
  shown = PAGE;
  const W = filterSpec();
  const filtered = chips.length > 0;
  const mode = F.sort.value;

  // Text, model and year queries and the dropdown/year filters read the text
  // index and the sighting rows, which arrive with detail.bin. A number search,
  // the "has chassis/contact" toggles and every sort work from core.bin alone.
  const ctx = query ? queryCtx(query) : null;
  const wantsDetail = W.eq.length || W.yMin !== null || W.yMax !== null
    || (ctx && (ctx.terms.length || ctx.qKeys.size || ctx.years.length));
  awaitingDetail = false;
  if (wantsDetail && !DETAIL) {
    hideWatch();
    awaitingDetail = !DETAIL_FAILED;
    $stats.textContent = DETAIL_FAILED
      ? t('detailFailedWait', 'البحث بالوصف والفلاتر يحتاج بقية السجل، وتعذّر تحميلها — حدّث الصفحة. البحث برقم الشاسي أو اللوحة يعمل الآن.')
      : t('detailWait', 'البحث بالوصف والفلاتر يحتاج بقية السجل — جارٍ تحميلها، وستظهر النتائج تلقائياً. البحث برقم الشاسي أو اللوحة يعمل الآن.');
    $res.innerHTML = '';
    $more.hidden = true; $shownLbl.textContent = '';
    return;   // loadDetail() renders again when detail.bin lands
  }

  if (!query) {
    hideWatch();
    curCtx = null;
    const order = orderBy(mode === 'best' ? 'latest' : mode);
    const browse = [];
    for (let k = 0; k < order.length; k++) {
      if (dossierPasses(order[k], W)) browse.push(order[k]);
      if ((k & (CHUNK - 1)) === CHUNK - 1) {
        await yieldToUI();
        if (gen !== renderGen) return;   // superseded by a newer render
      }
    }
    list = browse;
    // The in-loop guard is not enough: a superseded render that finished its
    // scan still reaches the DOM writes below and can clobber a NEWER render's
    // output. Measured — a stale "found" render called hideWatch() after a
    // newer "not found" render had shown the watch, and the watch vanished.
    if (gen !== renderGen) return;
    $stats.textContent = LANG === 'en'
      ? (filtered ? `${nf.format(list.length)} vehicles match the filters — type a number to search, or browse:`
                  : `${nf.format(list.length)} vehicles in the registry — type a chassis or plate number, or browse the latest:`)
      : (filtered ? `${nf.format(list.length)} عربة مطابقة للفلاتر — اكتب رقماً للبحث، أو تصفح:`
                  : `${nf.format(list.length)} عربة في السجل — اكتب رقم الشاسي أو اللوحة، أو تصفح الأحدث:`);
    if (!list.length) {
      $res.innerHTML = `<div class="empty">${t('emptyFilters', 'لا توجد عربات مطابقة لهذه الفلاتر.')}</div>`;
      $more.hidden = true; $shownLbl.textContent = '';
      return;
    }
    paint();
    return;
  }

  curCtx = ctx;
  // Scanning in the target display order means the common sorts need no sort
  // pass at all; only "best match" has to rank afterwards.
  const scanOrder = orderBy(mode === 'best' ? 'latest' : mode);
  const hits = [];
  for (let k = 0; k < scanOrder.length; k++) {
    const i = scanOrder[k];
    if (dossierPasses(i, W)) {
      const r = dscoreAt(i, ctx);
      if (r) { SCORE[i] = r.s; hits.push(i); }
    }
    if ((k & (CHUNK - 1)) === CHUNK - 1) {
      await yieldToUI();
      if (gen !== renderGen) return;   // superseded by a newer render
    }
  }
  list = hits;
  if (gen !== renderGen) return;   // see the note in the browse branch
  if (mode === 'best') {
    list.sort((a, b) => (SCORE[b] - SCORE[a])
      || ((IX.lat[b] > IX.lat[a]) - (IX.lat[b] < IX.lat[a])));
  }
  track('search', {
    search_kind: ctx.qd.length >= 4 ? 'identifier' : 'text',
    search_query: query,
    search_results: list.length,
  });
  $stats.textContent = list.length
    ? (LANG === 'en'
      ? `${nf.format(list.length)} possible vehicle${list.length === 1 ? '' : 's'}${filtered ? ' within the filters' : ''}:`
      : `${nf.format(list.length)} عربة محتملة${filtered ? ' ضمن الفلاتر' : ''}:`)
    : '';
  if (!list.length) {
    track('search_zero_results', { search_query: query });
    // The miss is the moment the watch is worth offering — and only then.
    showWatch(query);
    const idQuery = ctx.qd.length >= 4;
    $res.innerHTML = `<div class="empty">
      ${idQuery
        ? t('emptyId', 'لم نجد هذا الرقم في السجل — هذا لا يعني أن العربة غير موجودة، فقط أنها لم تُرصد في المنشورات بعد.')
        : t('emptyText', 'لا توجد نتائج لهذا البحث. جرّب رقم الشاسي أو اللوحة — البحث بالرقم أدق بكثير من الوصف.')}
      <br><br>${helpHtml()}</div>`;
    $more.hidden = true; $shownLbl.textContent = '';
    return;
  }
  hideWatch();
  paint();
}

// ---- standing watch ----
// Offered ONLY on a zero-result search. Clarity measured 21 of 22 searching
// sessions ending in nothing found; without this that is simply a dead end,
// and the one thing the registry never learns is what people came looking
// for. A watch turns the miss into a record we can re-check on every ingest.
const $watch = document.getElementById('watch-box');
const $watchForm = document.getElementById('watch-form');
const $watchMsg = document.getElementById('watch-msg');

function showWatch(query) {
  if (!$watch) return;
  const idField = document.getElementById('w-id');
  // Prefill with what they actually typed, but never overwrite something they
  // have already edited — losing a half-typed number would be worse than not
  // prefilling at all.
  if (idField && !idField.dataset.touched) idField.value = query;
  $watch.hidden = false;
  track('watch_shown');
}

function hideWatch() { if ($watch) $watch.hidden = true; }

if ($watchForm) {
  const idField = document.getElementById('w-id');
  if (idField) idField.addEventListener('input', () => { idField.dataset.touched = '1'; });

  // Clarity 2026-10-28: taps on "سجّل التنبيه / Register" were logged as dead.
  // A `required` field that is empty makes the browser show a small native
  // bubble and nothing else — on a phone that is easy to miss. Name the
  // missing field in the message area instead.
  const W_LABEL = {
    'w-id': ['رقم الشاسي أو اللوحة', 'the chassis or plate number'],
    'w-contact': ['وسيلة التواصل', 'a way to reach you'],
    'w-consent': ['الموافقة على حفظ البيانات', 'the consent box'],
  };
  let _invalid = [];
  $watchForm.addEventListener('invalid', (e) => {
    _invalid.push(e.target.id);
    if (_invalid.length > 1) return;   // one message, for the first field in form order
    queueMicrotask(() => {
      const first = _invalid[0]; _invalid = [];
      const [ar, en] = W_LABEL[first] || ['الحقل المطلوب', 'the required field'];
      $watchMsg.className = 'watch-msg err';
      $watchMsg.innerHTML = `أكمل ${ar} ثم اضغط «سجّل التنبيه».` +
        `<br><span dir="ltr">Fill in ${en}, then tap Register.</span>`;
      track('watch_invalid', { field: first });
    });
  }, true);
  const consent = document.getElementById('w-consent');
  if (consent) consent.addEventListener('change', () => {
    consent.closest('.consent').classList.toggle('on', consent.checked);
    if (consent.checked && $watchMsg.classList.contains('err')) $watchMsg.textContent = '';
  });

  $watchForm.addEventListener('submit', async (e) => {
    e.preventDefault();          // stay on the page; a redirect would lose the search
    const btn = document.getElementById('w-submit');
    btn.disabled = true;
    $watchMsg.className = 'watch-msg';
    $watchMsg.textContent = 'جارٍ التسجيل…';
    try {
      // Netlify Forms accepts a urlencoded POST to any path on the site; the
      // form-name field is what routes it to the right form.
      const body = new URLSearchParams(new FormData($watchForm)).toString();
      const resp = await fetch('/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body,
      });
      if (!resp.ok) throw new Error('HTTP ' + resp.status);
      $watchMsg.className = 'watch-msg ok';
      $watchMsg.innerHTML = 'تم التسجيل. سنتواصل معك إذا ظهر الرقم.' +
        '<br><span dir="ltr">Registered. We will contact you if the number appears.</span>';
      $watchForm.querySelectorAll('input').forEach(i => {
        if (i.type !== 'hidden') i.disabled = true;
      });
      track('watch_submit');
    } catch (err) {
      $watchMsg.className = 'watch-msg err';
      $watchMsg.innerHTML = 'تعذّر التسجيل. تأكد من اتصالك وحاول مرة أخرى.' +
        '<br><span dir="ltr">Could not register. Check your connection and try again.</span>';
      btn.disabled = false;
      track('watch_error');
    }
  });
}

// ---- listeners ----
for (const el of [F.model, F.color, F.status, F.location, F.sort]) el.addEventListener('change', render);
for (const el of [F.yearMin, F.yearMax]) el.addEventListener('input', render);
for (const el of [F.chassis, F.contact, F.loose]) el.addEventListener('change', render);
document.getElementById('f-reset').addEventListener('click', () => {
  for (const el of [F.model, F.color, F.status, F.location]) el.value = '';
  F.yearMin.value = F.yearMax.value = '';
  F.chassis.checked = F.contact.checked = F.loose.checked = false;
  F.sort.value = 'best';
  render();
});
$more.addEventListener('click', () => { shown += PAGE; paint(); track('show_more'); });
// Every tap on a card answers. Clarity, 2026-10-13: one visitor whose chassis
// search had just matched a car listed for sale tapped the date, source, status
// chip, ⚠️ chip, description, title and chassis line (13 dead clicks in 45s)
// before finding the small "original post" link. What people want from those
// taps is the post, or the rest of the card.
function toggleChipNote(chip) {
  const host = chip.closest('.tl-top, .top');
  if (!host) return;
  const next = host.nextElementSibling;
  const same = next && next.classList.contains('chip-note') && next.dataset.chip === chip.textContent;
  if (next && next.classList.contains('chip-note')) next.remove();
  if (same) return;
  const note = document.createElement('div');
  note.className = 'chip-note';
  note.dataset.chip = chip.textContent;
  note.textContent = chip.title;
  host.after(note);
}
$res.addEventListener('click', e => {
  if (!e.target.closest) return;
  const a = e.target.closest('a[href]');
  if (a) { track('post_link_open', { post_url: a.href }); return; }
  const sum = e.target.closest('details.tl-more > summary');
  if (sum) { if (!sum.parentElement.open) track('dossier_expand'); return; }
  if (e.target.closest('.chip-note')) return;
  const chip = e.target.closest('.chip[title]');
  if (chip) { toggleChipNote(chip); track('chip_explain'); return; }
  const row = e.target.closest('.tl-item');
  if (row) {
    if (row.dataset.url) {
      window.open(row.dataset.url, '_blank', 'noopener');
      track('post_link_open', { post_url: row.dataset.url, via: 'row' });
    } else {
      row.classList.toggle('open');   // unclamps the description
      track('row_open_nolink');
    }
    return;
  }
  const card = e.target.closest('.card');
  if (card && e.target.closest('.top, .idrow')) {
    const more = card.querySelector('details.tl-more');
    if (more) { more.open = !more.open; if (more.open) track('dossier_expand', { via: 'header' }); }
    else card.classList.toggle('open');
  }
});
// The stat tiles drew dead taps too; they answer with where the numbers come from.
document.getElementById('coverage').addEventListener('click', e => {
  if (!(e.target.closest && e.target.closest('.stat'))) return;
  const src = document.querySelector('details.sources');
  src.open = true;
  src.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
  track('coverage_tap');
});
document.getElementById('sources-list').addEventListener('click', e => {
  if (e.target.closest && e.target.closest('a')) track('source_open');
});
// The prompt and hint are <label for="q">, so a tap already focuses the box —
// but focus changes no DOM node, and Clarity counted those taps as dead
// (2026-10-28 heatmap). Mirror focus in a class: a visible cue for the
// visitor, and the heatmap stops reporting a working control as broken.
const $searchbox = document.querySelector('.searchbox');
$q.addEventListener('focus', () => $searchbox.classList.add('active'));
$q.addEventListener('blur', () => $searchbox.classList.remove('active'));
// The "N vehicles — enter your number" status line drew taps too.
document.getElementById('stats').addEventListener('click', () => $q.focus());
document.getElementById('share-btn').addEventListener('click', async () => {
  const url = 'https://mycarsd.online/?utm_source=share&utm_medium=button';
  const title = 'عربتي وين؟ — ابحث عن عربتك المفقودة برقم الشاسي أو اللوحة';
  track('share_click');
  if (navigator.share) { try { await navigator.share({ title, url }); return; } catch (e) {} }
  window.open('https://www.facebook.com/sharer/sharer.php?u=' + encodeURIComponent(url), '_blank', 'noopener');
});
F.sort.addEventListener('change', () => track('sort_change', { sort_mode: F.sort.value }));
for (const el of [F.model, F.color, F.status, F.location])
  el.addEventListener('change', () => { if (el.value) track('filter_use'); });
let _deb;
$q.addEventListener('input', () => { clearTimeout(_deb); _deb = setTimeout(render, 250); });

// ---- language ----
const ORIG = {
  sub: document.getElementById('sub').innerHTML,
  hint: document.getElementById('hint').textContent,
  notice: document.getElementById('notice-box').innerHTML,
  placeholder: $q.placeholder,
  yearFrom: F.yearMin.placeholder, yearTo: F.yearMax.placeholder,
  optModel: F.model.options[0].text, optColor: F.color.options[0].text,
  optStatus: F.status.options[0].text, optLocation: F.location.options[0].text,
  sortTexts: [...F.sort.options].map(o => o.text),
  lbChassis: document.getElementById('lb-chassis').textContent,
  lbContact: document.getElementById('lb-contact').textContent,
  lbLoose: document.getElementById('lb-loose').textContent,
  ask: document.getElementById('ask').innerHTML,
  hint: document.getElementById('hint').textContent,
  filtersSummary: document.getElementById('filters-summary').textContent,
  reset: document.getElementById('f-reset').textContent,
  updated: (document.getElementById('upd') || { textContent: '' }).textContent,
};
const wrapEl = document.querySelector('.wrap');
function applyLang() {
  const en = LANG === 'en';
  nf = nf0();
  wrapEl.dir = en ? 'ltr' : 'rtl';
  wrapEl.lang = en ? 'en' : 'ar';
  document.documentElement.dir = wrapEl.dir; document.documentElement.lang = wrapEl.lang;
  document.getElementById('lang-toggle').textContent = en ? 'عربي' : 'English';
  document.getElementById('sub').innerHTML = en ? EN.sub : ORIG.sub;
  document.getElementById('hint').textContent = en ? EN.hint : ORIG.hint;
  document.getElementById('notice-box').innerHTML = en ? EN.notice(ORIG.updated) : ORIG.notice;
  document.getElementById('official-help').innerHTML = '<br>' + helpHtml();
  $q.placeholder = en ? EN.placeholder : ORIG.placeholder;
  F.yearMin.placeholder = en ? EN.yearFrom : ORIG.yearFrom;
  F.yearMax.placeholder = en ? EN.yearTo : ORIG.yearTo;
  F.model.options[0].text = en ? EN.optModel : ORIG.optModel;
  F.color.options[0].text = en ? EN.optColor : ORIG.optColor;
  F.status.options[0].text = en ? EN.optStatus : ORIG.optStatus;
  F.location.options[0].text = en ? EN.optLocation : ORIG.optLocation;
  for (let i = 1; i < F.status.options.length; i++) {
    const o = F.status.options[i];
    o.text = `${t('st_' + o.value, STATUS_AR[o.value] || o.value)} (${nf.format(statusTally.get(o.value) || 0)})`;
  }
  const st = en ? EN.sortTexts : ORIG.sortTexts;
  [...F.sort.options].forEach((o, i) => { o.text = st[i]; });
  document.getElementById('lb-chassis').textContent = en ? EN.lbChassis : ORIG.lbChassis;
  document.getElementById('lb-contact').textContent = en ? EN.lbContact : ORIG.lbContact;
  document.getElementById('lb-loose').textContent = en ? EN.lbLoose : ORIG.lbLoose;
  document.getElementById('ask').innerHTML = en ? EN.ask : ORIG.ask;
  document.getElementById('hint').textContent = en ? EN.hint : ORIG.hint;
  document.getElementById('filters-summary').textContent = en ? EN.filtersSummary : ORIG.filtersSummary;
  document.getElementById('f-reset').textContent = en ? EN.reset : ORIG.reset;
  renderCoverage();
  render();
  try { localStorage.setItem('lang', LANG); } catch (e) {}
}
// The language button's listener is registered before the payload await (see
// showLoading), so it works during the download; from here on it calls this.
READY = true;
applyLang();

// ---- detail.bin: timelines, text index, row tables -------------------------
// Fetched after core so the two never compete for a thin connection. When it
// lands, a view that was waiting for it re-renders; a number-search view only
// repaints, so "show more" and the scroll position survive.
const $dt = document.getElementById('detail-status');
function showDetailStatus(got, total, parsing) {
  if (DETAIL) return;
  const en = LANG === 'en';
  $dt.hidden = false;
  $dt.textContent = parsing
    ? (en ? 'Preparing the sighting details…' : 'جارٍ تجهيز تفاصيل الرصدات…')
    : (en ? 'Loading the sighting details…' : 'جارٍ تحميل تفاصيل الرصدات…') + pct(got, total);
}
(async () => {
  showDetailStatus(0, 0, false);   // visible before the first byte, not after
  try {
    const D = await fetchPayload(DETAIL_URL, showDetailStatus);
    P.t = D.t;
    P.sg = D.sg;
    Object.assign(IX, D.ix);
    DETAIL = true;
    $dt.hidden = true;
  } catch (e) {
    DETAIL_FAILED = true;
    $dt.hidden = false;
    $dt.textContent = LANG === 'en'
      ? 'The sighting details could not be loaded — reload the page to try again. Searching by number still works.'
      : 'تعذّر تحميل تفاصيل الرصدات — حدّث الصفحة لإعادة المحاولة. البحث بالرقم ما زال يعمل.';
  }
  // Only a view that WAITED re-runs. A finished number search is already
  // right: re-rendering it would count the search twice in Clarity and could
  // reset a watch form the visitor is filling in on a zero-result page.
  if (awaitingDetail) render();
  else if (list.length) paint();
})();

</script>
</body>
</html>
"""


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


def fill_template(*, stats_js: str, fbkind_js: str, core_url: str,
                  detail_url: str, updated: str) -> str:
    return (TEMPLATE
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
    _AR_MARKS = re.compile("[\u0640\u064B-\u0670\u065F]")

    def tr_ar(value):
        return str(value or "").translate(_AR_DIGITS)

    def digits_only(value):
        return re.sub(r"\D", "", tr_ar(value))

    def norm_chassis(value):
        return re.sub(r"[^A-Z0-9]", "", tr_ar(value).upper())

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
