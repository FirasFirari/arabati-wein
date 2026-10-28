# Facebook Page log

Page: https://www.facebook.com/profile.php?id=61596074817604 · playbook: `social/PLAYBOOK.md`
One line per action. Read results with `python scripts\clarity_export.py --days 7 --dimensions Source Campaign`.

| date (UTC) | where | what | slug / UTM | result |
|---|---|---|---|---|
| 2026-10-28 | Page | created: name «عربتي وين؟ - Arabati Wein», category Community Service, bio, website (utm_campaign=about), email, profile + cover | — | live; username `arabatiwein` free but needs the account password to save |
| 2026-10-28 | Page | post `pinned` (intro + og.png photo), PINNED; comments: site link + guide link | utm_campaign=pinned | live |
| 2026-10-28 | Page | post `official` (the official free channels); comments: sudanpolice.net filing + guide link | utm_campaign=official | live |
| 2026-10-28 | GSC | property https://mycarsd.online/ verified (HTML tag); sitemap.xml submitted → Success, 8 URLs; indexing requested for /, guide, en, scams, yards | — | homepage already "on Google"; other four were unknown to Google |
| 2026-10-28 | Bing | Webmaster Tools needs an SSO sign-in (Microsoft/Google/Facebook) — left for the owner; IndexNow pings go out after every deploy (first ping HTTP 200) | — | pending owner |
| 2026-10-28 | Page | post `scams` (the four rules); comment: scams.html link | utm_campaign=scams | live |
| 2026-10-28 | group العربات المفقودة (28.2K, 627023535543636), personal profile | fresh post (no URL in body); comments: registry link, guide link, Page link | utm_campaign=g27k_b | live at once (no admin queue): https://www.facebook.com/groups/627023535543636/permalink/1436605364585445/ |
| 2026-10-28 | groups audit | August posts are STILL "Pending admin approval" in 330K (696207072195117), 150K (945879980039956), 128K (6326254187471594), 47K (859354772664822). Personal profile is NOT a member of 216K (6254926201235451), 122K (290795363310992), 105K (1485185051516519), 85K (996450738294918), 79K (1200135974768848); membership of 44K (937348630883545) is itself pending admin approval. | — | the group route is throttled by admins, not by us |
