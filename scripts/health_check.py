import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt import health

for name in ("channel_monitor", "intake_bot", "fb_service"):
    age = health.age_seconds(name)
    detail = (health.read(name) or {}).get("detail", "")
    if age is None:
        print(f"  {name:18s} NONE")
    else:
        state = "OK   " if age < 300 else "STALE"
        print(f"  {name:18s} {state} {int(age):>4}s  {detail}")
