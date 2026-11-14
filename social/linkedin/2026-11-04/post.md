# LinkedIn post: 75 days of Arabati Wein (4 November 2026)

Written to the LinkedIn formula in `Desktop\my self\11-style-guide.md` §6: scene, system,
bare numbers, clean stop. 97 words, no emoji, no em dashes, one `..`.

## Post

Every day, my home computer in Nairobi collects posts from the Facebook groups where Sudanese families search for cars the war took from them.

Many are photos of handwritten lists. Reposted across dozens of groups. Searched by hand, one scroll at a time, while scammers wait in the comments.

So I built one place to search them all, free, by chassis or plate number.

75 days in:
86,221 chassis numbers
184,313 posts
105 numbers on watch
2 leads passed to owners
0 recoveries confirmed

Our own car is still not in it..
Someone else's might be.

## First comment (keep the link out of the post body)

mycarsd.online
Free. No fees. Search by chassis or plate, and register the number if it isn't there yet.

## Images

Option A, a swipeable carousel: upload `arabati-wein-75-days.pdf` as a document. Title: "Arabati Wein: 75 days".

Option B, image post: upload `slide-1.png` to `slide-7.png` in order (1080x1350 each) with this alt text:

1. Cover. 86,221 distinct chassis numbers anyone can search. 184,313 public posts collected, 101,587 vehicles identified, 57,894 photos read by OCR, 105 numbers on watch. 22 August to 4 November 2026.
2. Line chart of running totals from 22 August to 4 November: posts collected rise steadily to 184,313; distinct chassis numbers jump to about 65,000 in the first week and reach 86,221. Four milestones marked: OCR on 29 August, the list parser on 1 September, reposts merged on 13 October, search engines and the Facebook Page on 28 October.
3. Bar chart of posts collected per day, mostly 2,000 to 5,000 a day with gaps when collection was paused. Below it, 239,631 photos collected and 57,894 read; 129,313 posts still waiting to be read.
4. Line chart of standing watches, from the first on 10 September to 105 on 4 November. 2 verified leads passed to families; 0 recoveries confirmed so far.
5. Stacked bars of visits per three-day window by source: 29, 47, 40, 39, then 83 on 4 November. ChatGPT and direct links throughout; Google from 20 October; Google, Bing and the site's own pages in the latest window.
6. Timeline of nine milestones from the first collected post on 22 August to 18 Facebook groups and doubled visits on 4 November.
7. Our own car is still not in it. Someone else's might be. Search free at mycarsd.online. No fees, no brokers. Official recovery goes through sudanpolice.net and the Traffic Department.

## Status

- 2026-11-04 ~20:40 (user said "post it"): published from Firas Mohammed's profile, audience Anyone:
  https://www.linkedin.com/feed/update/urn:li:share:7523780255095087105/
  Text verified line by line. **The seven images did NOT attach**: they uploaded into the composer
  (alt text added to all seven) but the published post's data has `content: null`. The Chrome window
  was minimized (0x0 viewport) during the upload, the likely cause. First comment NOT posted yet.
  Waiting on the owner: delete and repost with images, or keep the text post.
- Owner chose "1": the text-only post was deleted ("Post removed") and reposted with the Chrome window visible:
  **https://www.linkedin.com/feed/update/urn:li:activity:7523782048340189184/**
  Text verified line by line; all seven slides attached (LinkedIn's data carries all seven alt texts);
  first comment posted under it: "mycarsd.online / Free. No fees. Search by chassis or plate, and register
  the number if it isn't there yet."
- Lesson: LinkedIn drops media silently when the Chrome window is minimized during upload. Its
  Media button opens a native file picker; suppress it (patch HTMLInputElement.prototype.click for
  type=file) and upload straight into the input it creates.

## Checks done before writing

- The family car's chassis: no full or 8-digit match anywhere in the registry. Its last 6 digits appear only in the family's own intake report and in unrelated dealer phone numbers (checked 4 Nov, booleans only, number never printed).
- All numbers come from `registry_daily.csv`, `watches_daily.csv`, `traffic_sources.csv` and `snapshot.csv` in this folder (built 4 Nov 19:45). Leads and recoveries from `data/impact.json`.
- Re-render: `python make_linkedin.py` in this folder.
