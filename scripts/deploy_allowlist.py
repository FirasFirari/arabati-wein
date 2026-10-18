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
})
REQUIRED = frozenset({"index.html", "core.bin", "detail.bin"})
