"""Default-argument mutants (offline only): change one constant default of a
sqlglot function (bool flip, int +1, str edit, None -> 0/"" by context).
Usage: python mutate_default.py SQLGLOT_ROOT OUT_DIR COUNT SEED"""
import ast, difflib, os, random, sys
root, out, count, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
rng = random.Random(seed); os.makedirs(out, exist_ok=True)
py = sorted(os.path.relpath(os.path.join(dp, f), root) for dp, _, fn in os.walk(os.path.join(root, "sqlglot")) for f in fn if f.endswith(".py"))
sites = []
for rel in py:
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in n.args.defaults + [k for k in n.args.kw_defaults if k is not None]:
                if isinstance(d, ast.Constant) and d.lineno == d.end_lineno and type(d.value) in (bool, int, str):
                    sites.append((rel, d.lineno, d.col_offset, d.end_col_offset, d.value))
rng.shuffle(sites)
made = 0
for rel, ln, c0, c1, v in sites:
    if made >= count:
        break
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    lines = src.split("\n"); l = lines[ln - 1]
    if type(v) is bool:
        rep = repr(not v)
    elif type(v) is int:
        rep = repr(v + 1)
    else:
        rep = repr(v + "x") if v else repr("x")
    new = "\n".join(lines[:ln - 1] + [l[:c0] + rep + l[c1:]] + lines[ln:])
    try:
        ast.parse(new)
    except SyntaxError:
        continue
    d = difflib.unified_diff(src.splitlines(True), new.splitlines(True), "a/" + rel, "b/" + rel, n=3)
    open(os.path.join(out, "f%03d_default.patch" % (seed * 100 + made)), "w").write("diff --git a/%s b/%s\n" % (rel, rel) + "".join(d))
    made += 1
print(made, "of", len(sites), "default sites")
