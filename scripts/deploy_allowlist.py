"""The only files that may ever be published to mycarsd.online.

`scripts/netlify_deploy.py` publishes EVERY file in site/deploy, and a backup
left in that folder once went public for a minute. Both the builder and the
deployer import this list and refuse anything not on it.
"""
ALLOW = frozenset({
    "index.html", "core.bin", "detail.bin",
    "support.html", "privacy.html", "stats.html",
    "stats.json", "funding.json", "llms.txt",
    "robots.txt", "sitemap.xml", "_redirects", "_headers",
    # content pages (2026-10-28)
    "guide.html", "scams.html", "yards.html", "en.html",
    # brand assets: share card, icons, PWA manifest
    "og.png", "favicon.ico", "icon-192.png", "icon-512.png", "apple-touch-icon.png",
    "site.webmanifest",
    # IndexNow key file (scripts/indexnow.py)
    "indexnow-key.txt",
})
REQUIRED = frozenset({"index.html", "core.bin", "detail.bin"})
