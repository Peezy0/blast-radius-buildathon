"""Targeted mutants: swap `and` <-> `or` in a two-operand BoolOp in sqlglot
(offline only), in any context (conditions and value positions alike), plus
harmless edits (comment / blank line insertions) as no-change controls.
Usage: python mutate_boolop.py SQLGLOT_ROOT OUT_DIR COUNT SEED [HARMLESS]"""
import ast, difflib, os, random, sys
root, out, count, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
harmless = int(sys.argv[5]) if len(sys.argv) > 5 else 0
rng = random.Random(seed); os.makedirs(out, exist_ok=True)
py = sorted(os.path.relpath(os.path.join(dp, f), root) for dp, _, fn in os.walk(os.path.join(root, "sqlglot")) for f in fn if f.endswith(".py"))
def emit(name, rel, src, new):
    d = difflib.unified_diff(src.splitlines(True), new.splitlines(True), "a/" + rel, "b/" + rel, n=3)
    open(os.path.join(out, name), "w").write("diff --git a/%s b/%s\n" % (rel, rel) + "".join(d))
sites = []
for rel in py:
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.BoolOp) and len(n.values) == 2:
            a, b = n.values
            if a.end_lineno == b.lineno:
                sites.append((rel, a.end_lineno, a.end_col_offset, b.col_offset, isinstance(n.op, ast.And)))
rng.shuffle(sites)
made = 0
for rel, line, c0, c1, isand in sites:
    if made >= count:
        break
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    lines = src.split("\n"); l = lines[line - 1]
    mid = l[c0:c1]; o, n = ("and", "or") if isand else ("or", "and")
    if mid.strip().strip("()").strip() != o:
        continue
    nl = l[:c0] + mid.replace(o, n, 1) + l[c1:]
    new = "\n".join(lines[:line - 1] + [nl] + lines[line:])
    try:
        ast.parse(new)
    except SyntaxError:
        continue
    emit("b%03d_boolop.patch" % (seed * 100 + made), rel, src, new)
    made += 1
h = 0
while h < harmless:
    rel = rng.choice(py)
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    lines = src.split("\n")
    stmts = [s.lineno for s in ast.walk(ast.parse(src)) if isinstance(s, ast.stmt)]
    if not stmts:
        continue
    ln = rng.choice(stmts); l = lines[ln - 1]
    ind = l[: len(l) - len(l.lstrip())]
    ins = ind + "# review note" if rng.random() < 0.5 else ""
    new = "\n".join(lines[:ln - 1] + [ins] + lines[ln - 1:])
    try:
        if ast.dump(ast.parse(new)) != ast.dump(ast.parse(src)):
            continue
    except SyntaxError:
        continue
    emit("h%03d_harmless.patch" % (seed * 100 + h), rel, src, new)
    h += 1
print(made, "of", len(sites), "boolop sites;", h, "harmless")
