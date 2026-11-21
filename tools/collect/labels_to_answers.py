"""Turn label JSONs into an answers.json for eval_select.py (offline only).
Usage: python labels_to_answers.py TESTS_JSON MUT_DIR"""
import json, os, sys
tests = json.load(open(sys.argv[1]))["tests"]
num = {t["id"]: t["number"] for t in tests}
mut = sys.argv[2]
commits = {}
for fn in sorted(os.listdir(os.path.join(mut, "labels"))):
    if not fn.endswith(".json"):
        continue
    cid = fn[:-5]
    d = json.load(open(os.path.join(mut, "labels", fn)))
    fail = {num[n] for n in d["failed"] if n in num}
    unknown = [n for n in d["failed"] if n not in num]
    for f in d["collect_errors"]:
        fail |= {t["number"] for t in tests if t["file"] == f}
    if unknown:
        print(cid, "unmapped failing ids", unknown[:3], file=sys.stderr)
    commits[cid] = {"failing": sorted(fail), "kind": cid.split("_", 1)[1]}
# eval set = labelled commits only
os.makedirs(os.path.join(mut, "eval", "commits"), exist_ok=True)
for cid in commits:
    src = os.path.join(mut, "commits", cid + ".patch")
    dst = os.path.join(mut, "eval", "commits", cid + ".patch")
    if not os.path.exists(dst):
        os.link(src, dst)
dst = os.path.join(mut, "eval", "tests.json")
if not os.path.exists(dst):
    os.link(sys.argv[1], dst) if False else open(dst, "w").write(open(sys.argv[1]).read())
json.dump({"commits": commits}, open(os.path.join(mut, "eval", "answers.json"), "w"))
print(len(commits), "labelled;", sum(1 for c in commits.values() if c["failing"]), "with failures")
