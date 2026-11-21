"""Targeted mutants: swap a comparison operator with its boundary/identity
twin (< <=, > >=, is ==, is not !=) in sqlglot (offline only).
Usage: python mutate_cmpop.py SQLGLOT_ROOT OUT_DIR COUNT SEED"""
import ast, difflib, os, random, sys
root, out, count, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
rng = random.Random(seed); os.makedirs(out, exist_ok=True)
SWAP = {ast.Lt: "<=", ast.LtE: "<", ast.Gt: ">=", ast.GtE: ">", ast.Is: "==", ast.Eq: "is",
        ast.IsNot: "!=", ast.NotEq: "is not"}
TXT = {ast.Lt: "<", ast.LtE: "<=", ast.Gt: ">", ast.GtE: ">=", ast.Is: "is", ast.Eq: "==",
       ast.IsNot: "is not", ast.NotEq: "!="}
py = sorted(os.path.relpath(os.path.join(dp, f), root) for dp, _, fn in os.walk(os.path.join(root, "sqlglot")) for f in fn if f.endswith(".py"))
sites = []
for rel in py:
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, ast.Compare) and len(n.ops) == 1 and type(n.ops[0]) in SWAP:
            r = n.comparators[0]
            if isinstance(r, ast.Constant) and r.value is None:
                continue
            if n.left.end_lineno != r.lineno:
                continue
            sites.append((rel, n.left.end_lineno, n.left.end_col_offset, r.col_offset, type(n.ops[0])))
rng.shuffle(sites)
made = 0
for rel, line, c0, c1, op in sites:
    if made >= count:
        break
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    lines = src.split("\n"); l = lines[line - 1]
    mid = l[c0:c1]
    if mid.strip() != TXT[op]:
        continue
    nl = l[:c0] + mid.replace(TXT[op], SWAP[op], 1) + l[c1:]
    new = "\n".join(lines[:line - 1] + [nl] + lines[line:])
    try:
        ast.parse(new)
    except SyntaxError:
        continue
    d = difflib.unified_diff(src.splitlines(True), new.splitlines(True), "a/" + rel, "b/" + rel, n=3)
    open(os.path.join(out, "o%03d_cmpop.patch" % (seed * 100 + made)), "w").write("diff --git a/%s b/%s\n" % (rel, rel) + "".join(d))
    made += 1
print(made, "of", len(sites), "sites")
