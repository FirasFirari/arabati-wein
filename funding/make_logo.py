from PIL import Image, ImageDraw, ImageFont, features
import math, os, shutil

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

BG = (15, 76, 92)        # deep teal
CAR = (255, 255, 255)
ACC = (240, 178, 60)     # amber accent (magnifier / question)

# rounded-square background
d.rounded_rectangle((0, 0, S - 1, S - 1), radius=200, fill=BG)

# --- car silhouette (side view), centred, drawn as polygons/ellipses ---
cx, cy = S * 0.46, S * 0.56
w = S * 0.66
# body
body_l, body_r = cx - w / 2, cx + w / 2
body_top, body_bot = cy - S * 0.03, cy + S * 0.11
d.rounded_rectangle((body_l, body_top, body_r, body_bot), radius=60, fill=CAR)
# cabin
cab_l, cab_r = cx - w * 0.30, cx + w * 0.26
cab_top = cy - S * 0.18
d.polygon([(cab_l, body_top + 10), (cab_l + 70, cab_top), (cab_r - 40, cab_top), (cab_r + 40, body_top + 10)], fill=CAR)
# windows (cut-outs in bg colour)
d.polygon([(cab_l + 40, body_top + 4), (cab_l + 90, cab_top + 30), (cx - 20, cab_top + 30), (cx - 20, body_top + 4)], fill=BG)
d.polygon([(cx + 20, body_top + 4), (cx + 20, cab_top + 30), (cab_r - 60, cab_top + 30), (cab_r + 10, body_top + 4)], fill=BG)
# wheels
r = S * 0.075
for wx in (cx - w * 0.30, cx + w * 0.30):
    d.ellipse((wx - r, body_bot - r * 0.6, wx + r, body_bot + r * 1.4), fill=BG)
    d.ellipse((wx - r, body_bot - r * 0.6, wx + r, body_bot + r * 1.4), outline=CAR, width=34)
    d.ellipse((wx - r * 0.35, body_bot + r * 0.05, wx + r * 0.35, body_bot + r * 0.75), fill=CAR)

# --- magnifying glass with question mark, top-right, overlapping the car ---
mx, my, mr = S * 0.72, S * 0.30, S * 0.16
d.ellipse((mx - mr, my - mr, mx + mr, my + mr), fill=BG, outline=ACC, width=46)
# handle
ang = math.radians(45)
hx1, hy1 = mx + mr * math.cos(ang), my + mr * math.sin(ang)
hx2, hy2 = mx + (mr + S * 0.14) * math.cos(ang), my + (mr + S * 0.14) * math.sin(ang)
d.line((hx1, hy1, hx2, hy2), fill=ACC, width=70)
d.ellipse((hx2 - 35, hy2 - 35, hx2 + 35, hy2 + 35), fill=ACC)
# question mark inside the lens
font = None
for cand in [r"C:\Windows\Fonts\arialbd.ttf", r"C:\Windows\Fonts\segoeuib.ttf", r"C:\Windows\Fonts\arial.ttf"]:
    if os.path.exists(cand):
        font = ImageFont.truetype(cand, int(mr * 1.5)); break
if font is None:
    font = ImageFont.load_default()
txt = "?"
bb = d.textbbox((0, 0), txt, font=font)
tw, th = bb[2] - bb[0], bb[3] - bb[1]
d.text((mx - tw / 2 - bb[0], my - th / 2 - bb[1]), txt, font=font, fill=ACC)

out = os.path.join(os.path.dirname(__file__), "arabati-wein-logo.png")
img.save(out)
img.resize((512, 512), Image.LANCZOS).save(out.replace(".png", "-512.png"))
img.resize((64, 64), Image.LANCZOS).save(out.replace(".png", "-64.png"))
shutil.copy(out, r"C:\Users\User\Desktop\Arabati-Wein-logo.png")
print("saved", out, "raqm:", features.check("raqm"))
