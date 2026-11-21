"""Offline evaluation of src/covselect.py on a labelled set (sample or mutants).
Usage: python tools/collect/eval_select.py MAP.json.gz DATA_DIR ANSWERS.json"""
import gzip, json, sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
from src.covselect import select, Unknown
from src.scheduling import optimize

cmap = json.load(gzip.open(sys.argv[1], "rt"))
data = pathlib.Path(sys.argv[2])
answers = json.load(open(sys.argv[3]))["commits"]
cat = json.load(open(data / "tests.json"))["tests"]
dur = {t["number"]: t["seconds"] for t in cat}
id2num = {t["id"]: t["number"] for t in cat}
mapnum = [id2num.get(i) for i in cmap["tests"]]
unknown_ids = set(dur) - set(n for n in mapnum if n is not None)
def cost(workers):
    if not workers: return 0
    return max(sum(round(dur[t]*1000) for t in w) for w in workers) + 2000*len(workers)
total = 0; misses = 0; fb = 0
for p in sorted((data / "commits").glob("*.patch")):
    cid = p.stem
    try:
        sel = select(p.read_text(), cmap)
        sel = {mapnum[i] for i in sel if mapnum[i] is not None} | unknown_ids
        tag = ""
    except Unknown as e:
        sel = set(dur); tag = "FULL(%s)" % e; fb += 1
    w = optimize(dur, frozenset(sel)) if sel else []
    c = cost(w); fail = set(answers[cid]["failing"]); miss = bool(fail - sel)
    total += c + 300000*miss; misses += miss
    print(f"{cid} {answers[cid]['kind']:8} sel={len(sel):4} fail={len(fail):3} cost={c/1000:7.3f} {'MISS '+str(sorted(fail-sel)[:5]) if miss else ''} {tag}")
print(f"TOTAL {total/1000:.3f}s misses={misses} fallbacks={fb}")
