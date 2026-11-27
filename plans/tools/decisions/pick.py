"""Private: pick the moved items to hand-trace: every decision-3 item and a spread of decision-2 transitions (ids only)."""
import json, random, sys
from collections import defaultdict
moved = json.load(open(sys.argv[1]))
random.seed(20261126)
by = defaultdict(list)
for m in moved:
    by[(m["cls"], m["signal"], m["b"]["work_state"], m["a"]["work_state"])].append(m["fid"])
want = []
for k, ids in sorted(by.items()):
    ids = sorted(ids)
    n = len(ids) if k[0] != "d2" else (4 if len(ids) >= 40 else 3 if len(ids) >= 15 else 2 if len(ids) >= 5 else len(ids))
    pick = random.sample(ids, min(n, len(ids)))
    want += pick
    print(k, len(ids), "->", len(pick))
for extra in sys.argv[2:]:
    if extra not in want:
        want.append(extra)
open(sys.argv[1] + ".pick", "w").write("\n".join(want) + "\n")
print("picked", len(want))
