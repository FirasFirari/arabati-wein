# Facebook Page playbook — عربتي وين؟ - Arabati Wein

Page: https://www.facebook.com/profile.php?id=61596074817604 (created 2026-10-28 from the
owner's everyday account; username `arabatiwein` is reserved in the Page username setting
but needs the account password to save, which Claude cannot enter).

Assets: `social/fb-profile.png` (1024², the logo) and `social/fb-cover.png` (1640×856,
text in the top band because Facebook centres the profile picture over the bottom).
Regenerate both with `python scripts\make_brand_assets.py`.

## Rules (same as facebook_posts.md, plus the Page)

1. **No URL in a post body.** Facebook downranks outbound links. Body first, link as the
   FIRST COMMENT, always UTM-tagged:
   `https://mycarsd.online/?utm_source=facebook&utm_medium=page&utm_campaign=<slug>`
   Deep links work too: `/guide.html?utm_...`, `/?q=<number>&utm_...`.
2. **Never the same text twice.** Each Page post has its own copy; each group share has its
   own opener. Identical copy across groups is the spam signal.
3. **Pace.** Page timeline: at most 1–2 posts a day. Group shares from the personal profile:
   1–2 groups a day, largest never-posted groups first (list in config.yaml), and check
   `facebook.com/groups/<id>/my_pending_content` before counting a post as live.
4. **Never from the collector profile** (`data/browser_profile`). A restriction there kills
   the harvester.
5. **Never a phone number, never a WhatsApp number, never the family's own chassis.**
   The official route is sudanpolice.net + the Traffic Department in person. The old
   "traffic WhatsApp" number in HANDOFF's correction note is PERSONAL and must never appear.
6. **Engagement as the Page** only on public Pages' posts (news about looted cars, the
   state committee's announcements). Groups do not accept Pages as commenters; reply there
   as the personal profile if at all, and only to people asking how to search.
7. **Log everything** in `social/facebook_page_log.md`: date, where, slug, result.

## Weekly routine (15 minutes)

- `python scripts\page_stats_post.py` → paste as a Page post (body) + first comment (link).
- Share one of the week's Page posts into 1–2 groups from the personal profile with a fresh
  one-line opener.
- Read results: `python scripts\clarity_export.py --days 7 --dimensions Source Campaign`
  → sessions with `Source=facebook` and `Campaign=page*` / `g*`.
- Reply to every comment on the Page within a day. Anyone asking "where is my car" gets the
  four lines: search on the site, register a watch, file at sudanpolice.net, never pay.

## Post library (slug → body). Link goes in the first comment.

### `pinned` — what this is (PINNED)
عربتي وين؟ سجل مجاني للعربات المفقودة والمنهوبة في حرب السودان.

جمعنا المنشورات العامة من عشرات مجموعات وقنوات العربات المفقودة والمضبوطة، واستخرجنا منها أرقام الشاسي واللوحات، وحطيناها في مكان واحد تقدر تبحث فيهو من الموبايل.

كيف تستخدمه:
١. اكتب رقم الشاسي أو اللوحة. يكفي جزء من الرقم، وبالأرقام العربية أو الإنجليزية.
٢. إن ظهر الرقم تلقى كل المنشورات التي ورد فيها مع رابط المنشور الأصلي.
٣. إن ما ظهر، سجّل تنبيهاً مجانياً: نفحص المنشورات الجديدة كل ساعة ونبلغك إن ظهر رقمك.

مجاني، بلا تسجيل، ولا نطلب رقم الشاسي في الخاص أبداً. الرابط في أول تعليق.

### `howto` — how to search (with phone screenshots)
طريقة البحث في ثلاث خطوات، بالصور:
١. افتح الموقع من الموبايل واكتب رقم الشاسي أو اللوحة في الخانة.
٢. يكفي جزء من الرقم، مثلاً آخر ست أو سبع خانات من الشاسي.
٣. إن لم يُعثر على الرقم تظهر لك خانة «سجّل تنبيهاً»: اترك الرقم ووسيلة تواصل ونبلغك إن ظهر.

ملاحظة: عدم وجود الرقم لا يعني أن العربة ضاعت، بل أن أحداً لم ينشر عنها بعد. الرابط في أول تعليق.

### `official` — the official free channels
الاسترداد الرسمي مجاني، وهذه هي الطرق الرسمية بالترتيب:
١. بلّغ إلكترونياً في منصة الشرطة sudanpolice.net (رابط البلاغ في أول تعليق). البلاغ يضع حظراً قانونياً على ترخيص العربة ويدرجها في منصة الإنتربول.
٢. اذهب بنفسك إلى إدارة المرور أو فرعية مكافحة سرقة السيارات، واسأل عن أحواش الحجز.
٣. الأحواش الرسمية تُفحص برقم الشاسي عند البوابة. خذ إثبات هوية ومستندات الملكية.

لا يوجد رقم واتساب رسمي للمرور أو الشرطة. أي حساب يقدّم نفسه كذلك ليس جهة رسمية. الدليل الكامل خطوة خطوة في أول تعليق.

### `scams` — scam warning
أربع قواعد تحميك من كل نصب «استرجاع العربات»:
١. لا يوجد رقم واتساب رسمي للمرور أو الشرطة.
٢. لا ترسل رقم الشاسي في رسائل خاصة لأي «منصة» أو «لجنة».
٣. لا تدفع مالاً مقدماً ولا «رسوم إفراج». الاسترداد الرسمي مجاني.
٤. لا تُملِ رمز التحقق المكوّن من ٦ أرقام لأي شخص، مهما كان السبب.

صور الأحواش الحكومية حقيقية، والرقم المطبوع فوقها ليس لها. التفاصيل والأساليب المتكررة في أول تعليق.

### `yards` — impound yards
وين العربات المجمَّعة؟ أعلنت إدارة المرور ولجان الولاية عن أحواش منها:
• حوش المرور، شارع الأربعين، أم درمان
• حوش المرور، الخرطوم بحري، شمال كبري الحلفايا
• أحواش لجنة سحب وتجميع المركبات بولاية الخرطوم
• حوش المرور بولاية الجزيرة

شروط الاستلام كما أعلنتها دائرة المرور: إثبات هوية، مستندات الملكية، وإقرار مشفوع باليمين من المحكمة، ثم مراجعة الحوش والضابط المسؤول في المكان. الحوش يُفحص بالشاسي عند البوابة. الطريق إليه هو إدارة المرور، لا واتساب. التفاصيل في أول تعليق.

### `stats` — weekly numbers
`python scripts\page_stats_post.py`

### `en` — English
Arabati Wein is a free public registry of vehicles looted or lost in the Sudan war. It collects the public posts from dozens of Sudanese lost-vehicle groups, extracts the chassis and plate numbers, and lets you search them in one place from a phone. No fees, no account. If your number is not there, register a watch and we tell you when it appears. Official recovery is free: file at sudanpolice.net and attend the Traffic Department in person; there is no official WhatsApp number. Link in the first comment.

## Group-share openers (one per group, personal profile, never reused)

- «لمن لسه بيفتش: سجل مجاني تبحث فيهو برقم الشاسي، جمعنا فيهو منشورات القروبات دي كلها. الرابط في التعليق الأول.»
- «قبل ما ترسل رقم الشاسي لأي زول في الخاص، ابحث عنه بنفسك هنا مجاناً. التفاصيل في التعليق.»
- «الكشوفات المنشورة في القروبات اتجمعت في مكان واحد تقدر تبحث فيهو بآخر أرقام الشاسي. الرابط في أول تعليق.»
- «الأحواش الرسمية وشروط الاستلام كما أعلنتها إدارة المرور، في صفحة واحدة. الرابط في التعليق.»
- «لو عربيتك ما ظهرت لسه: سجّل رقمها وبنبلغك إن ظهر في أي كشف جديد. مجاناً، وبدون ما ترسل الرقم لأي زول.»
