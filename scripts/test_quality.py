"""Quick quality gate test for batch results."""
import json
import os
from discovery.quality_gate import run_quality_gate_batch

batch = os.path.expanduser("~/.discovery/data/batch")
results = []
for f in sorted(os.listdir(batch)):
    if f.endswith(".json"):
        with open(os.path.join(batch, f)) as fh:
            results.append(json.load(fh))

if not results:
    print("No results in batch")
    exit()

report = run_quality_gate_batch(results)
print(f"Total: {report['total']}, Passed: {report['passed']}, Failed: {report['failed']}")
for v in report["verdicts"]:
    pid = v["paper_id"]
    score = v["score"]
    passed = v["pass"]
    flags = v["flags"]
    blocks = v["blocks"]
    print(f"  {pid}: score={score} pass={passed}")
    if flags:
        for f in flags:
            print(f"    FLAG: {f}")
    if blocks:
        for b in blocks:
            print(f"    BLOCK: {b}")
