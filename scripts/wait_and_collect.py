"""Wait for all open batches to finish, then collect them.

`collect` already skips batches that are still processing, so this just polls
until the state file is empty (or we give up), then runs the final steps.
"""
import subprocess
import sys
import time

sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
from carhunt.batch_analyze import _load_state

ROOT = r"C:\Users\User\Desktop\Car Hunt"
DEADLINE = time.time() + 3 * 3600  # batches are guaranteed within 24h; 3h is plenty


def run(args):
    return subprocess.run([sys.executable, "-m"] + args, cwd=ROOT,
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace")


while time.time() < DEADLINE:
    open_batches = _load_state().get("batches", [])
    if not open_batches:
        break
    result = run(["carhunt.batch_analyze", "collect"])
    print(result.stdout[-4000:], flush=True)
    if result.stderr.strip():
        print("STDERR:", result.stderr[-1500:], flush=True)
    if not _load_state().get("batches", []):
        break
    time.sleep(180)

print("\n=== collection finished, refreshing registry ===", flush=True)
# newly extracted vehicles need model keys, then the page is regenerated
keys = subprocess.run([sys.executable, r"scripts\recompute_model_keys.py"],
                      cwd=ROOT, capture_output=True, text=True,
                      encoding="utf-8", errors="replace")
print(keys.stdout.splitlines()[0] if keys.stdout else keys.stderr[-500:], flush=True)
body = subprocess.run([sys.executable, r"scripts\repair_bodytypes.py"],
                      cwd=ROOT, capture_output=True, text=True,
                      encoding="utf-8", errors="replace")
print(body.stdout.splitlines()[0] if body.stdout else "", flush=True)
print(run(["carhunt.export_site"]).stdout, flush=True)
print("READY_TO_PUBLISH", flush=True)
