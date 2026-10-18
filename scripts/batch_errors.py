import sys
sys.path.insert(0, r"C:\Users\User\Desktop\Car Hunt")
import anthropic

client = anthropic.Anthropic()
batch_id = sys.argv[1]
kinds = {}
shown = 0
for result in client.messages.batches.results(batch_id):
    t = result.result.type
    kinds[t] = kinds.get(t, 0) + 1
    if t != "succeeded" and shown < 3:
        shown += 1
        err = getattr(result.result, "error", None)
        print(f"\n--- {result.custom_id}: {t}")
        print(repr(err)[:1200])
print("\ncounts:", kinds)
