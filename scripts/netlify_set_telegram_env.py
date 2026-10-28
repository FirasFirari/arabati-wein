"""Give the Netlify function the Telegram secrets it needs, from config.yaml.

site/functions/submission-created.js announces every new watch registration
to Telegram the instant Netlify receives it. It reads TELEGRAM_BOT_TOKEN and
TELEGRAM_NOTIFY_CHAT_ID from the site's environment variables (scope:
functions). This script copies telegram.bot_token and telegram.notify_chat_id
from config.yaml into those variables - run it once, and again if the bot
token is ever rotated. It prints nothing secret.

    python scripts\netlify_set_telegram_env.py
    python scripts\netlify_set_telegram_env.py --show   # list keys only

Token: NETLIFY_AUTH_TOKEN or site/netlify_token.txt, as the deploy script.
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import requests

from carhunt.config import PROJECT_ROOT, load_config

API = "https://api.netlify.com/api/v1"


def netlify_token() -> str:
    tok = os.environ.get("NETLIFY_AUTH_TOKEN", "").strip()
    if tok:
        return tok
    for p in sorted((PROJECT_ROOT / "site").glob("[Nn]etlify_token.txt")):
        tok = p.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    sys.exit("No Netlify token: set NETLIFY_AUTH_TOKEN or create site/netlify_token.txt.")


def main() -> None:
    cfg = load_config()
    sid = ((cfg.get("netlify") or {}).get("site_id") or os.environ.get("NETLIFY_SITE_ID") or "")
    if not sid:
        sys.exit("No Netlify site id in config.yaml (netlify.site_id).")
    tg = cfg.get("telegram") or {}
    bot, chat = str(tg.get("bot_token") or ""), str(tg.get("notify_chat_id") or "")
    if not bot or not chat:
        sys.exit("config.yaml needs telegram.bot_token and telegram.notify_chat_id.")

    H = {"Authorization": f"Bearer {netlify_token()}"}
    site = requests.get(f"{API}/sites/{sid}", headers=H, timeout=30)
    site.raise_for_status()
    acct = site.json()["account_slug"]
    env_url = f"{API}/accounts/{acct}/env"

    if "--show" in sys.argv:
        r = requests.get(env_url, params={"site_id": sid}, headers=H, timeout=30)
        r.raise_for_status()
        for e in r.json():
            print(f"  {e['key']:<26} scopes={e.get('scopes')} secret={e.get('is_secret')}")
        return

    wanted = {
        "TELEGRAM_BOT_TOKEN": {"value": bot, "secret": True},
        "TELEGRAM_NOTIFY_CHAT_ID": {"value": chat, "secret": False},
    }
    existing = {e["key"] for e in requests.get(
        env_url, params={"site_id": sid}, headers=H, timeout=30).json()}
    # The free plan rejects both a narrowed scope ("Upgrade your Netlify
    # account to set specific scopes") and, on some accounts, is_secret. Try
    # the tight version first and fall back; say which one took.
    for key, spec in wanted.items():
        attempts = [
            ("scope functions, secret", {"scopes": ["functions"], "is_secret": spec["secret"]}),
            ("all scopes, secret", {"is_secret": spec["secret"]}),
            ("all scopes, plain", {}),
        ]
        if not spec["secret"]:
            attempts = [("scope functions", {"scopes": ["functions"]}), ("all scopes", {})]
        done = False
        for label, extra in attempts:
            body = {"key": key, "values": [{"value": spec["value"], "context": "all"}], **extra}
            if key in existing:
                r = requests.put(f"{env_url}/{key}", params={"site_id": sid}, headers=H,
                                 json=body, timeout=30)
            else:
                r = requests.post(env_url, params={"site_id": sid}, headers=H,
                                  json=[body], timeout=30)
            if r.ok:
                print(f"  {'updated' if key in existing else 'created'} {key} ({label})")
                existing.add(key)
                done = True
                break
            print(f"  {key}: {label} refused (HTTP {r.status_code} {r.text[:120]})")
        if not done:
            sys.exit(f"could not set {key}")
    print("done. The function picks these up on its next invocation; no redeploy needed.")


if __name__ == "__main__":
    main()
