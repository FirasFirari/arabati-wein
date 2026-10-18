html = open(r"C:\Users\User\Desktop\Car Hunt\site\arabati-wein.html", encoding="utf-8").read()
tokens = ['id="more"', "const PAGE = 40", "function paint()", "عرض المزيد",
          "shownLbl", "shown += PAGE", 'class="pager"']
for t in tokens:
    print(f"  {t:22s}", "ok" if t in html else "MISSING")
