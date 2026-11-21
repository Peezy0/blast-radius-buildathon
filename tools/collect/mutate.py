"""Generate synthetic single-site mutants of the pinned sqlglot as unified diffs.

Offline validation only: the mutants and their labels are never shipped in the
image and the selector never reads them.
Usage: python mutate.py SQLGLOT_ROOT OUT_DIR COUNT SEED
"""
import ast, difflib, os, random, re, sys

root, out, count, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
rng = random.Random(seed)
os.makedirs(out, exist_ok=True)

py = []
for dp, dn, fn in os.walk(os.path.join(root, "sqlglot")):
    for f in fn:
        if f.endswith(".py"):
            py.append(os.path.relpath(os.path.join(dp, f), root))
fixtures = []
for dp, dn, fn in os.walk(os.path.join(root, "tests/fixtures/optimizer")):
    for f in fn:
        if f.endswith(".sql"):
            fixtures.append(os.path.relpath(os.path.join(dp, f), root))
py.sort(); fixtures.sort()

OPS = [(" and ", " or "), (" or ", " and "), (" == ", " != "), (" != ", " == "),
       (" < ", " <= "), (" > ", " >= "), (" is not ", " is "), (" not in ", " in "),
       ("True", "False"), ("False", "True"), (" + 1", " - 1"), ("return not ", "return ")]
COMMENTS = ["# Cheap check first", "# Kept for backwards compatibility", "# Intentionally not cached", "# TODO: revisit"]

def diff(rel, old, new):
    a = old.splitlines(keepends=True); b = new.splitlines(keepends=True)
    d = difflib.unified_diff(a, b, "a/" + rel, "b/" + rel, n=3)
    return "diff --git a/%s b/%s\n" % (rel, rel) + "".join(d)

def code_mutant():
    rel = rng.choice(py)
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    lines = src.split("\n")
    sites = []
    for i, l in enumerate(lines):
        s = l.strip()
        if not s or s.startswith(("#", '"', "'")):
            continue
        for a, b in OPS:
            if a in l:
                sites.append((i, a, b))
        for m in re.finditer(r"(?<![\w.])(\d+)(?![\w.])", l):
            sites.append((i, ("num", m.start(), m.end()), None))
    rng.shuffle(sites)
    for i, a, b in sites:
        l = lines[i]
        if isinstance(a, tuple):
            _, s0, s1 = a
            nl = l[:s0] + str(int(l[s0:s1]) + 1) + l[s1:]
        else:
            k = l.index(a); nl = l[:k] + b + l[k + len(a):]
        new = lines[:i] + [nl] + lines[i + 1:]
        ns = "\n".join(new)
        try:
            if ast.dump(ast.parse(ns)) == ast.dump(ast.parse(src)):
                continue
        except SyntaxError:
            continue
        return rel, src, ns, "code"
    return None

def comment_mutant():
    rel = rng.choice(py)
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    lines = src.split("\n")
    tree = ast.parse(src)
    stmts = [n for n in ast.walk(tree) if isinstance(n, ast.stmt) and n.lineno == n.end_lineno]
    n = rng.choice(stmts)
    ind = re.match(r"\s*", lines[n.lineno - 1]).group(0)
    new = lines[: n.lineno - 1] + [ind + rng.choice(COMMENTS)] + lines[n.lineno - 1:]
    return rel, src, "\n".join(new), "comment"

def fixture_mutant():
    rel = rng.choice(fixtures)
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    lines = src.split("\n")
    cand = [i for i, l in enumerate(lines) if l.strip() and not l.startswith(("#", "--"))]
    if not cand:
        return None
    i = rng.choice(cand)
    words = lines[i].split(" ")
    k = rng.randrange(len(words) + 1)
    words.insert(k, rng.choice(["x", "1", "0", "SELECT"]))
    new = lines[:i] + [" ".join(words)] + lines[i + 1:]
    return rel, src, "\n".join(new), "fixture"

made = 0
while made < count:
    r = rng.random()
    m = code_mutant() if r < 0.65 else (fixture_mutant() if r < 0.85 else comment_mutant())
    if not m:
        continue
    rel, old, new, kind = m
    name = "m%03d_%s" % (seed * 1000 + made, kind)
    with open(os.path.join(out, name + ".patch"), "w") as f:
        f.write(diff(rel, old, new))
    made += 1
