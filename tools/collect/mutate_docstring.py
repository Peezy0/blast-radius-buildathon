"""Docstring-text mutants (offline only): change one word inside a
module/class/function docstring of sqlglot.
Usage: python mutate_docstring.py SQLGLOT_ROOT OUT_DIR COUNT SEED"""
import ast, difflib, os, random, re, sys
root, out, count, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
rng = random.Random(seed); os.makedirs(out, exist_ok=True)
py = sorted(os.path.relpath(os.path.join(dp, f), root) for dp, _, fn in os.walk(os.path.join(root, "sqlglot")) for f in fn if f.endswith(".py"))
sites = []
for rel in py:
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    for n in ast.walk(ast.parse(src)):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef)) and n.body and isinstance(n.body[0], ast.Expr) \
                and isinstance(n.body[0].value, ast.Constant) and isinstance(n.body[0].value.value, str):
            d = n.body[0].value
            for ln in range(d.lineno, d.end_lineno + 1):
                sites.append((rel, ln))
rng.shuffle(sites)
made = 0
for rel, ln in sites:
    if made >= count:
        break
    src = open(os.path.join(root, rel), encoding="utf-8").read()
    lines = src.split("\n"); l = lines[ln - 1]
    words = [m for m in re.finditer(r"\b(the|a|an|is|of|to|and|or|if|in)\b", l)]
    if not words:
        continue
    m = rng.choice(words)
    rep = {"the": "this", "a": "one", "an": "one", "is": "was", "of": "for", "to": "into",
           "and": "or", "or": "and", "if": "when", "in": "within"}[m.group(1)]
    new = "\n".join(lines[:ln - 1] + [l[:m.start()] + rep + l[m.end():]] + lines[ln:])
    try:
        t0, t1 = ast.parse(src), ast.parse(new)
    except SyntaxError:
        continue
    if ast.dump(t0) == ast.dump(t1):
        continue  # was a comment, not the docstring
    d = difflib.unified_diff(src.splitlines(True), new.splitlines(True), "a/" + rel, "b/" + rel, n=3)
    open(os.path.join(out, "s%03d_docstring.patch" % (seed * 100 + made)), "w").write("diff --git a/%s b/%s\n" % (rel, rel) + "".join(d))
    made += 1
print(made, "of", len(sites), "docstring lines")
