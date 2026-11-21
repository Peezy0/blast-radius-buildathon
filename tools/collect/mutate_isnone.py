"""Validation mutants for the `is None` <-> `== None` equivalence (offline only).
Usage: python mutate_isnone.py SQLGLOT_ROOT OUT_DIR COUNT SEED"""
import difflib, os, random, sys
root, out, count, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
rng = random.Random(seed); os.makedirs(out, exist_ok=True)
sites = []
for dp, _, fn in os.walk(os.path.join(root, "sqlglot")):
    for f in fn:
        if f.endswith(".py"):
            rel = os.path.relpath(os.path.join(dp, f), root)
            for i, l in enumerate(open(os.path.join(root, rel), encoding="utf-8").read().split("\n")):
                if not l.strip().startswith("#") and (" is None" in l or " is not None" in l):
                    sites.append((rel, i))
rng.shuffle(sites)
for k, (rel, i) in enumerate(sorted(sites[:count])):
    src = open(os.path.join(root, rel), encoding="utf-8").read(); lines = src.split("\n")
    l = lines[i]
    l = l.replace(" is not None", " != None", 1) if " is not None" in l else l.replace(" is None", " == None", 1)
    new = "\n".join(lines[:i] + [l] + lines[i + 1:])
    d = difflib.unified_diff(src.splitlines(True), new.splitlines(True), "a/" + rel, "b/" + rel, n=3)
    open(os.path.join(out, "n%03d_isnone.patch" % k), "w").write("diff --git a/%s b/%s\n" % (rel, rel) + "".join(d))
print(len(sites), "sites")
