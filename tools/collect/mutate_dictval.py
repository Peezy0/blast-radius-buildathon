"""Targeted mutants: edit a constant inside a VALUE of a class-body dict literal
(keys untouched; offline only). Usage: python mutate_dictval.py SQLGLOT_ROOT OUT_DIR COUNT SEED"""
import ast, difflib, os, random, re, sys
root, out, count, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
rng = random.Random(seed); os.makedirs(out, exist_ok=True)
py = sorted(os.path.relpath(os.path.join(dp, f), root) for dp, _, fn in os.walk(os.path.join(root, "sqlglot")) for f in fn if f.endswith(".py"))
sites = []
for rel in py:
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    tree = ast.parse(src)
    for cls in ast.walk(tree):
        if not isinstance(cls, ast.ClassDef):
            continue
        for st in cls.body:
            if isinstance(st, (ast.Assign, ast.AnnAssign)) and isinstance(st.value, ast.Dict):
                for n in (m for val in st.value.values for m in ast.walk(val)):
                    if isinstance(n, ast.Constant) and n.lineno == n.end_lineno and type(n.value) in (bool, int, str) and not isinstance(n.value, bool) or (isinstance(n, ast.Constant) and isinstance(n.value, bool)):
                        sites.append((rel, n.lineno, n.col_offset, n.end_col_offset, n.value))
rng.shuffle(sites)
made = 0
for rel, line, c0, c1, v in sites:
    if made >= count:
        break
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    lines = src.split("\n"); l = lines[line - 1]
    if isinstance(v, bool): nv = repr(not v)
    elif isinstance(v, int): nv = repr(v + 1)
    else:
        if not v or len(v) > 30: continue
        nv = repr(v.upper() if v != v.upper() else v.lower() + "x")
    nl = l[:c0] + nv + l[c1:]
    new = "\n".join(lines[:line - 1] + [nl] + lines[line:])
    try:
        if ast.dump(ast.parse(new)) == ast.dump(ast.parse(src)): continue
    except SyntaxError:
        continue
    d = difflib.unified_diff(src.splitlines(True), new.splitlines(True), "a/" + rel, "b/" + rel, n=3)
    open(os.path.join(out, "d%03d_dictval.patch" % (seed * 100 + made)), "w").write("diff --git a/%s b/%s\n" % (rel, rel) + "".join(d))
    made += 1
print(made, "of", len(sites), "sites")
