"""Build the baseline dependency map from a br_collect run at the pinned commit.

Usage (inside the collection image, which has coverage installed):
  python build_map.py SQLGLOT_ROOT COLLECT_OUT TESTS_JSON OUT_MAP_JSON_GZ

Everything here derives from running the pinned sqlglot test-suite; no patch or
answer data is read.
"""
import ast, gzip, json, os, sys, collections
import coverage

root, cov_dir, tests_json, out_path = sys.argv[1:5]
catalog = json.load(open(tests_json))
test_ids = [t["id"] for t in catalog["tests"]]
index = {tid: i for i, tid in enumerate(test_ids)}

def class_key(nodeid):  # "file::Class" or "file" for function tests
    parts = nodeid.split("::")
    return "::".join(parts[:2]) if len(parts) > 2 else parts[0]

members = collections.defaultdict(set)
for tid, i in index.items():
    members[class_key(tid)].add(i)
    members[tid.split("::")[0]].add(i)

def ctx_tests(ctx):
    """Tests a context is attributed to (setup/teardown fan out to the class)."""
    if ctx.startswith("collect|"):  # static context prefix added by coverage
        ctx = ctx[len("collect|"):]
    if "|" not in ctx:
        return None
    nodeid, phase = ctx.rsplit("|", 1)
    if nodeid not in index:
        return set()
    if phase in ("setup", "teardown"):
        return set(members[class_key(nodeid)])
    return {index[nodeid]}

_c = coverage.Coverage(data_file=os.path.join(cov_dir, ".coverage"))
_c.load()
_c.combine(data_paths=[cov_dir], keep=True)  # merge ProcessPoolExecutor worker data
_c.save()
data = coverage.CoverageData(os.path.join(cov_dir, ".coverage"))
data.read()
lines_by_file = {}
exec_by_file = collections.defaultdict(set)   # tests executing any line of file
module_ctx_lines = collections.defaultdict(set)
for path in data.measured_files():
    rel = os.path.relpath(path, root)
    by_line = collections.defaultdict(set)
    for line, ctxs in data.contexts_by_lineno(path).items():
        for ctx in ctxs:
            ts = ctx_tests(ctx)
            if ts is None:
                module_ctx_lines[rel].add(line)
                continue
            by_line[line] |= ts
    lines_by_file[rel] = by_line

opens = json.load(open(os.path.join(cov_dir, "opens.json")))
types_ = json.load(open(os.path.join(cov_dir, "types.json")))
for fn in sorted(os.listdir(cov_dir)):
    for prefix, target in (("worker-opens-", opens), ("worker-types-", types_)):
        if fn.startswith(prefix):
            for k, v in json.load(open(os.path.join(cov_dir, fn))).items():
                target[k] = sorted(set(target.get(k, [])) | set(v))

# Source files and their structure.
files = {}
for base in ("sqlglot", "tests"):
    for dp, dn, fn in os.walk(os.path.join(root, base)):
        for f in fn:
            if f.endswith(".py"):
                rel = os.path.relpath(os.path.join(dp, f), root)
                files[rel] = open(os.path.join(dp, f), encoding="utf-8").read()

def modname(rel):
    m = rel[:-3].replace("/", ".")
    return m[:-9] if m.endswith(".__init__") else m

def mod_to_file():
    return {modname(r): r for r in files}
M2F = mod_to_file()

# Instantiation evidence: "module:qualname" -> tests
inst = collections.defaultdict(set)
for ctx, names in types_.items():
    ts = ctx_tests(ctx)
    if not ts:
        continue
    for n in names:
        inst[n] |= ts

# Readers (non-import opens) and directory listers.
readers = collections.defaultdict(set)
listers = collections.defaultdict(set)
for ctx, paths in opens.items():
    ts = ctx_tests(ctx)
    if not ts:
        continue
    for p in paths:
        if p.endswith("/"):
            listers[p.rstrip("/") or "."] |= ts
        elif p.endswith(".py"):
            # an import executes module-level lines in this context; a text read does not
            by_line = lines_by_file.get(p, {})
            tree_mod_lines = MODLINES.get(p) if "MODLINES" in globals() else None
            readers.setdefault(p + "#maybe", set()).update(ts)
        else:
            readers[p] |= ts

# Module-level statement lines (to tell imports from text reads).
MODLINES = {}
classes = {}
imports = collections.defaultdict(set)
for rel, src in files.items():
    try:
        tree = ast.parse(src)
    except SyntaxError:
        continue
    MODLINES[rel] = {n.lineno for n in tree.body}
    cls = {}
    def walk(node, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.ClassDef):
                q = prefix + ch.name
                cls[q] = (ch.lineno, ch.end_lineno)
                walk(ch, q + ".")
            elif isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(ch, prefix + ch.name + ".<locals>.")
    walk(tree, "")
    classes[rel] = cls
    pkg = modname(rel).split(".")
    if not rel.endswith("__init__.py"):
        pkg = pkg[:-1]
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                imports[rel].add(a.name)
        elif isinstance(n, ast.ImportFrom):
            if n.level:
                b = ".".join(pkg[: len(pkg) - n.level + 1])
                m = b + ("." + n.module if n.module else "")
            else:
                m = n.module or ""
            imports[rel].add(m)
            for a in n.names:
                imports[rel].add(m + "." + a.name)

for key in [k for k in readers if k.endswith("#maybe")]:
    p = key[:-6]
    ts = readers.pop(key)
    modl = MODLINES.get(p, set())
    by_line = lines_by_file.get(p, {})
    importers_ctx = set()
    for l in modl:
        importers_ctx |= by_line.get(l, set())
    text = ts - importers_ctx
    if text:
        readers[p] |= text

importers = collections.defaultdict(set)  # file -> files importing it
for rel, mods in imports.items():
    for m in mods:
        f = M2F.get(m)
        if f and f != rel:
            importers[f].add(rel)

def tests_executing(rel):
    s = set()
    for ts in lines_by_file.get(rel, {}).values():
        s |= ts
    return s

def instantiators_of_module(rel):
    m = modname(rel)
    s = set()
    for q in classes.get(rel, {}):
        s |= inst.get(m + ":" + q, set())
    return s

out_files = {}
for rel, src in files.items():
    by_line = lines_by_file.get(rel, {})
    execd = tests_executing(rel)
    module_tests = execd | instantiators_of_module(rel)
    for imp in importers.get(rel, ()):
        module_tests |= instantiators_of_module(imp)
        if imp.startswith("tests/"):
            module_tests |= tests_executing(imp)
    if rel.startswith("tests/"):
        module_tests |= members.get(rel, set())
    class_tests = {}
    m = modname(rel)
    for q, (a, b) in classes.get(rel, {}).items():
        s = set(inst.get(m + ":" + q, set()))
        for l in range(a, b + 1):
            s |= by_line.get(l, set())
        # nested classes: also anyone instantiating an enclosing class
        parts = q.split(".")
        for k in range(1, len(parts)):
            s |= inst.get(m + ":" + ".".join(parts[:k]), set())
        if rel.startswith("tests/"):
            s |= members.get(rel + "::" + parts[0], set())
        class_tests[q] = s
    sets, set_ids = [], {}
    def sid(s):
        key = tuple(sorted(s))
        if key not in set_ids:
            set_ids[key] = len(sets); sets.append(list(key))
        return set_ids[key]
    out_files[rel] = {
        "source": src,
        "lines": {str(l): sid(ts) for l, ts in sorted(by_line.items()) if ts},
        "module": sid(module_tests),
        "classes": {q: sid(s) for q, s in class_tests.items()},
        "readers": sid(readers.get(rel, set())),
        "sets": sets,
    }

data_files = {p: sorted(ts) for p, ts in readers.items() if not p.endswith(".py")}
result = {
    "version": 2,
    "baseline_commit": "eb11aec4be8cb2122afe713a4816662c3f0fb2fa",
    "tests": test_ids,
    "files": out_files,
    "data": data_files,
    "dirs": {d: sorted(ts) for d, ts in listers.items()},
}
with gzip.open(out_path, "wt", encoding="utf-8", compresslevel=9) as f:
    json.dump(result, f, separators=(",", ":"))
print("files", len(out_files), "data", len(data_files), "dirs", len(listers), file=sys.stderr)
