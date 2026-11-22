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
    if ctx.startswith("collect:"):
        # importing/collecting a test module: a failure there fails every test in it
        return set(members.get(ctx[len("collect:"):], set()))
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
import_mods_by_line = collections.defaultdict(lambda: collections.defaultdict(set))
for path in data.measured_files():
    rel = os.path.relpath(path, root)
    by_line = collections.defaultdict(set)
    for line, ctxs in data.contexts_by_lineno(path).items():
        for ctx in ctxs:
            ts = ctx_tests(ctx)
            if ts is None:
                module_ctx_lines[rel].add(line)
                c2 = ctx[len("collect|"):] if ctx.startswith("collect|") else ctx
                if c2.startswith("import:"):
                    import_mods_by_line[rel][line].add(c2[len("import:"):])
                continue
            by_line[line] |= ts
            if ctx.startswith(("collect:", "collect|collect:")):
                module_ctx_lines[rel].add(line)  # also runs before any test
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
import_inst = collections.defaultdict(set)  # "module:qualname" -> modules whose import created one
for ctx, names in types_.items():
    if ctx.startswith("import:"):
        for n in names:
            import_inst[n].add(ctx[len("import:"):])
        continue
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
        if ctx.startswith("collect:") and p.endswith(".py"):
            continue  # importing a test module opens its (and helpers') source
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

# ---- comparison infection evidence (br_collect comparison recorder) ----
cmp_tests = collections.defaultdict(set)   # "rel:line:col" -> tests
cmp_import = set()
cmp_files = set()
for fn in sorted(os.listdir(cov_dir)):
    if fn == "cmp.json" or fn.startswith("worker-cmp-"):
        d = json.load(open(os.path.join(cov_dir, fn)))
        cmp_files.update(d.get("files", []))
        cmp_import.update(d.get("import", []))
        for ctx, nids in d.get("obs", {}).items():
            ts = ctx_tests(ctx)
            if ts is None:
                cmp_import.update(nids)
                continue
            for n in nids:
                cmp_tests[n] |= ts

# Tests whose coverage differs between repeated runs (unstable_tests.py): the
# comparison evidence may come from fewer runs than the line coverage, so the
# selector adds these tests wherever they execute the edited comparison.
unstable_lines = collections.defaultdict(lambda: collections.defaultdict(set))  # rel -> line -> tests
_up = os.path.join(cov_dir, "unstable.json")
if os.path.exists(_up):
    for _t, _per in json.load(open(_up))["lines"].items():
        if _t in index:
            for _rel, _ls in _per.items():
                for _l in _ls:
                    unstable_lines[_rel][_l].add(index[_t])
elif cmp_files:
    sys.exit("comparison evidence needs unstable.json (tools/collect/unstable_tests.py)")

# ---- class data-attribute reads ----
attr_reads = collections.defaultdict(set)
import_keys = set()
instrumented = set()
dictkeys = {}
import_sites = collections.defaultdict(set)
attr_files = [os.path.join(cov_dir, "attrs.json")] + [
    os.path.join(cov_dir, f) for f in sorted(os.listdir(cov_dir)) if f.startswith("worker-attrs-")]
have_attrs = os.path.exists(attr_files[0])
for fn in attr_files if have_attrs else []:
    d = json.load(open(fn))
    import_keys.update(d["import"])
    instrumented.update(d.get("instrumented", []))
    dictkeys.update(d.get("dictkeys", {}))
    for rk, site in d.get("import_sites", []):
        import_sites[rk].add(site)
    for ctx, keys in d["reads"].items():
        ts = ctx_tests(ctx)
        if ts is None:
            import_keys.update(keys)  # collection/between reads behave like import-time
            continue
        for k in keys:
            attr_reads[k] |= ts

def _static_import_names():
    """Names read off cls/klass in __init_subclass__ and metaclass methods (they
    run at class creation, before the new class's own attributes are tracked)."""
    names = set()
    for rel, src in files.items():
        if not rel.startswith("sqlglot/"):
            continue
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                is_meta = any(isinstance(b, ast.Name) and b.id == "type" for b in node.bases)
                for fn in node.body:
                    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and (
                        fn.name == "__init_subclass__" or (is_meta and fn.name in ("__new__", "__init__", "__call__"))
                    ):
                        for m in ast.walk(fn):
                            if isinstance(m, ast.Attribute):
                                names.add(m.attr)
                            elif isinstance(m, ast.Constant) and isinstance(m.value, str):
                                names.add(m.value)
    return names
STATIC_IMPORT_NAMES = _static_import_names() if have_attrs else set()

def _definition_time_names(tree):
    """Attribute names read at module/class-body level of this module (they run
    while the module executes, before its classes are instrumented)."""
    names = set()
    def walk(node, in_func):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                # decorators and defaults evaluate at definition time
                for d in getattr(ch, "decorator_list", []):
                    walk(d, in_func)
                walk(ch.args, in_func)
                walk_body = ch.body if isinstance(ch.body, list) else [ch.body]
                for b in walk_body:
                    walk(b, True)
                continue
            if isinstance(ch, ast.Attribute) and not in_func:
                names.add(ch.attr)
            if isinstance(ch, ast.Name) and isinstance(ch.ctx, ast.Load) and not in_func:
                names.add(ch.id)  # bare names in class bodies read earlier class attrs
            walk(ch, in_func)
    walk(tree, False)
    return names

def _bare_definition_names(tree):
    """Bare names loaded at module/class-body level: in a class body they read
    the namespace entry before the class (and its wrapped dicts) exist."""
    names = set()
    def walk(node, in_func):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for d in getattr(ch, "decorator_list", []):
                    walk(d, in_func)
                walk(ch.args, in_func)
                continue
            if isinstance(ch, ast.Name) and isinstance(ch.ctx, ast.Load) and not in_func \
                    and not _keys_only_use(node, ch):
                names.add(ch.id)
            walk(ch, in_func)
    walk(tree, False)
    return names

def _keys_only_use(parent, name):
    """A dict used only for its keys: iterated, tested with `in`, or len()."""
    if isinstance(parent, (ast.comprehension, ast.For)) and parent.iter is name:
        return True
    if isinstance(parent, ast.Compare) and parent.left is not name and all(
            isinstance(o, (ast.In, ast.NotIn)) for o in parent.ops):
        return True
    if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Name) \
            and parent.func.id in ("len", "set", "frozenset", "sorted", "list", "tuple") \
            and len(parent.args) == 1 and parent.args[0] is name and not parent.keywords:
        return True
    return False

# ---- class-body dict literals: per-key value readers, through copies ----
_trees = {}
def _src_tree(rel):
    if rel not in _trees:
        _trees[rel] = ast.parse(files[rel]) if rel in files else None
    return _trees[rel]

def _names_in(node):
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}

def _copy_target(site):
    """If an import-time read at `site` is a class-body dict display copying a
    dict's entries unchanged (`**D` or `**{k: v for k, v in D.items() if <k only>}`),
    return the receiving class attribute's key, else None."""
    path, _, line = site.rpartition(":")
    try:
        line = int(line)
    except ValueError:
        return None
    rel = os.path.relpath(path, root)
    tree = _src_tree(rel)
    if tree is None:
        return None
    found = []
    def walk(node, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.ClassDef):
                q = prefix + ch.name
                for b in ch.body:
                    if isinstance(b, ast.ClassDef):
                        continue
                    if b.lineno <= line <= b.end_lineno:
                        found.append((q, b))
                walk(ch, q + ".")
    walk(tree, "")
    if len(found) != 1:
        return None
    q, st = found[0]
    if not (isinstance(st, ast.Assign) and len(st.targets) == 1 and isinstance(st.targets[0], ast.Name)
            and isinstance(st.value, ast.Dict)):
        return None
    def copy_spread(v):
        if isinstance(v, (ast.Name, ast.Attribute)):
            return True
        if isinstance(v, ast.DictComp) and len(v.generators) == 1:
            g = v.generators[0]
            return (isinstance(v.key, ast.Name) and isinstance(v.value, ast.Name) and not g.is_async
                    and isinstance(g.target, ast.Tuple) and len(g.target.elts) == 2
                    and all(isinstance(e, ast.Name) for e in g.target.elts)
                    and g.target.elts[0].id == v.key.id and g.target.elts[1].id == v.value.id
                    and v.key.id != v.value.id
                    and isinstance(g.iter, ast.Call) and not g.iter.args and not g.iter.keywords
                    and isinstance(g.iter.func, ast.Attribute) and g.iter.func.attr == "items"
                    and not any(v.value.id in _names_in(c) for c in g.ifs))
        return False
    elems = list(zip(st.value.keys, st.value.values))
    if line == st.lineno and elems and all(
            (k.lineno if k is not None else v.lineno) > line for k, v in elems):
        # dict-merge reads are reported on the display's first line
        spreads = [v for k, v in elems if k is None]
        if spreads and all(copy_spread(v) for v in spreads):
            return modname(rel) + ":" + q + "." + st.targets[0].id
        return None
    for k, v in elems:
        if not (v.lineno <= line <= v.end_lineno):
            continue
        if k is not None:
            return None  # read inside a key/value expression
        if isinstance(v, (ast.Name, ast.Attribute)):
            return modname(rel) + ":" + q + "." + st.targets[0].id
        if isinstance(v, ast.DictComp) and len(v.generators) == 1:
            g = v.generators[0]
            if (isinstance(v.key, ast.Name) and isinstance(v.value, ast.Name) and not g.is_async
                    and isinstance(g.target, ast.Tuple) and len(g.target.elts) == 2
                    and all(isinstance(e, ast.Name) for e in g.target.elts)
                    and g.target.elts[0].id == v.key.id and g.target.elts[1].id == v.value.id
                    and v.key.id != v.value.id
                    and isinstance(g.iter, ast.Call) and not g.iter.args and not g.iter.keywords
                    and isinstance(g.iter.func, ast.Attribute) and g.iter.func.attr == "items"
                    and not any(v.value.id in _names_in(c) for c in g.ifs)):
                return modname(rel) + ":" + q + "." + st.targets[0].id
        return None
    return None

_bare_cache = {}
def _dict_base_ok(dkey):
    """Readers of this dict's values are all recorded (wrapped before use)."""
    toks = dictkeys.get(dkey)
    if not toks or None in toks:
        return False
    m, _, qa = dkey.partition(":")
    name = qa.rsplit(".", 1)[-1]
    rel = M2F.get(m)
    if rel is None:
        return False
    if rel not in _bare_cache:
        _bare_cache[rel] = _bare_definition_names(_src_tree(rel))
    return name not in _bare_cache[rel]

SUBPROCESS_TESTS = {index[t] for t in index if t.endswith("::test_lazy_load")}

# ---- required-argument evidence (br_reqargs run, optional) ----
# A class's `required_args` is derived from its arg_types values when the class
# is created and is read only by error_messages, which reports key k only when
# argument k is missing. A value edit of key k therefore reaches exactly the
# tests where error_messages ran on an instance of a class taking its arg_types
# from that dict while argument k was missing.
reqargs_missing = collections.defaultdict(set)   # (class key, arg key) -> tests
reqargs_import = set()                            # pairs seen outside any test
reqargs_users = collections.defaultdict(set)      # arg_types dict key -> class keys
_rq_files = [os.path.join(cov_dir, f) for f in sorted(os.listdir(cov_dir)) if f.startswith("reqargs.json.")]
for fn in _rq_files:
    d = json.load(open(fn))
    for cls, owner in d["owners"].items():
        if owner is not None:
            reqargs_users[owner].add(cls)
    for ctx, pairs in d["missing"].items():
        ts = ctx_tests(ctx)
        for cls, k in pairs:
            if ts is None:
                reqargs_import.add((cls, k))
            else:
                reqargs_missing[(cls, k)] |= ts

def _required_args_ok():
    """`required_args` is assigned only by the derivation and read only inside
    error_messages (checked over every sqlglot module and test file)."""
    if not _rq_files:
        return False
    for rel in files:
        tree = _src_tree(rel)
        if tree is None:
            return False
        stack = [(tree, None)]
        while stack:
            node, fn = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fn = node.name
            for ch in ast.iter_child_nodes(node):
                stack.append((ch, fn))
            name = node.attr if isinstance(node, ast.Attribute) else (
                node.id if isinstance(node, ast.Name) else (node.value if isinstance(node, ast.Constant) else None))
            if name != "required_args":
                continue
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store) and fn == "__init_subclass__":
                continue
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load) and fn == "error_messages":
                continue
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store) and fn is None:
                continue  # class-level default (every subclass re-derives it)
            return False
    return True

_RQ_OK = None
def _reqargs_site(site):
    """True when `site` is `cls.required_args = {k for k, v in cls.<D>.items() if v}`."""
    global _RQ_OK
    path, _, line = site.rpartition(":")
    try:
        line = int(line)
    except ValueError:
        return False
    tree = _src_tree(os.path.relpath(path, root))
    if tree is None:
        return False
    for st in ast.walk(tree):
        if not (isinstance(st, ast.Assign) and st.lineno <= line <= st.end_lineno):
            continue
        if not (len(st.targets) == 1 and isinstance(st.targets[0], ast.Attribute)
                and st.targets[0].attr == "required_args" and isinstance(st.value, ast.SetComp)):
            return False
        v = st.value
        if len(v.generators) != 1:
            return False
        g = v.generators[0]
        ok = (isinstance(g.target, ast.Tuple) and len(g.target.elts) == 2
              and all(isinstance(e, ast.Name) for e in g.target.elts)
              and isinstance(v.elt, ast.Name) and v.elt.id == g.target.elts[0].id
              and isinstance(g.iter, ast.Call) and isinstance(g.iter.func, ast.Attribute)
              and g.iter.func.attr == "items" and len(g.ifs) == 1
              and isinstance(g.ifs[0], ast.Name) and g.ifs[0].id == g.target.elts[1].id)
        if not ok:
            return False
        if _RQ_OK is None:
            _RQ_OK = _required_args_ok()
        return _RQ_OK
    return False

def reqargs_readers(dkey, tok):
    users = reqargs_users.get(dkey)
    if not users:
        return None
    out = set(SUBPROCESS_TESTS)
    for cls in users:
        ks = [k for (c, k) in reqargs_keys.get(cls, ()) if repr(k) == tok]
        for k in ks:
            if (cls, k) in reqargs_import:
                return None
            out |= reqargs_missing.get((cls, k), set())
    return out

reqargs_keys = collections.defaultdict(set)
for (c, k) in list(reqargs_missing) + list(reqargs_import):
    reqargs_keys[c].add((c, k))

# ---- scalar tokenizer flag evidence (br_tokflags run, optional) ----
# Tokenizer._init_core copies scalar flags into TokenizerCore fields; a flag's
# value matters only where that field (or the flag itself, elsewhere) is read.
tok_uses = collections.defaultdict(set)   # flag definition key -> tests
tok_bad = set()                           # keys used outside any test
tok_flags = {}
tok_unknown = set()                       # flags read from a core of unknown origin
_tk_files = [os.path.join(cov_dir, f) for f in sorted(os.listdir(cov_dir)) if f.startswith("tok.json.")]
for fn in _tk_files:
    d = json.load(open(fn))
    tok_flags.update(d["flags"])
    for ctx, keys in d["uses"].items():
        ts = ctx_tests(ctx)
        for k in keys:
            if k.startswith("?:"):
                tok_unknown.add(k[2:])
            elif ts is None:
                tok_bad.add(k)
            else:
                tok_uses[k] |= ts

_tok_static = {}
def _tok_static_ok(name):
    """Every load of attribute `name` in sqlglot runs inside a function (so the
    recording descriptor sees it), and in tokens.py only inside _init_core."""
    if name in _tok_static:
        return _tok_static[name]
    ok = True
    for rel in files:
        if not rel.startswith("sqlglot/"):
            continue
        tree = _src_tree(rel)
        if tree is None:
            ok = False; break
        stack = [(tree, None)]
        while stack and ok:
            node, fn = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                fn = getattr(node, "name", "<lambda>")
            for ch in ast.iter_child_nodes(node):
                stack.append((ch, fn))
            hit = (isinstance(node, ast.Attribute) and node.attr == name and isinstance(node.ctx, ast.Load)) or (
                isinstance(node, ast.Constant) and node.value == name)
            if not hit:
                continue
            if fn is None or (rel == "sqlglot/tokens.py" and fn != "_init_core"):
                ok = False
        if not ok:
            break
    _tok_static[name] = ok
    return ok

def tok_flag_readers(key, name):
    if not _tk_files or name not in tok_flags or name in tok_unknown or key in tok_bad:
        return None
    if not _tok_static_ok(name):
        return None
    return tok_uses.get(key, set()) | SUBPROCESS_TESTS

_kr_memo = {}
def key_readers(dkey, tok):
    """Tests that can observe a change to dkey's value under key token tok, or
    None when some reader is not attributable (import-time use, unknown copy)."""
    memo = (dkey, tok)
    if memo in _kr_memo:
        return _kr_memo[memo]
    _kr_memo[memo] = None  # cycle guard
    out = None
    if _dict_base_ok(dkey) and tok in dictkeys[dkey]:
        out = set(attr_reads.get(dkey + "\x00" + tok, set())) | attr_reads.get(dkey + "\x00*", set())
        for rk in (dkey + "\x00" + tok, dkey + "\x00*"):
            if rk in import_keys:
                sites = import_sites.get(rk, set())
                if not sites:
                    out = None; break
                for site in sites:
                    if rk.endswith("\x00*") and _reqargs_site(site):
                        sub = reqargs_readers(dkey, tok)
                        if sub is None:
                            out = None; break
                        out |= sub
                        continue
                    tgt = _copy_target(site)
                    if tgt is None or tgt == dkey:
                        out = None; break
                    if tok not in (dictkeys.get(tgt) or ()):
                        if _dict_base_ok(tgt):
                            continue  # filtered out of the copy
                        out = None; break
                    sub = key_readers(tgt, tok)
                    if sub is None:
                        out = None; break
                    out |= sub
                if out is None:
                    break
    _kr_memo[memo] = out
    return out

def base_module_tests(rel):
    mt = tests_executing(rel) | instantiators_of_module(rel)
    for imp in importers.get(rel, ()):
        mt |= instantiators_of_module(imp)
        if imp.startswith("tests/"):
            mt |= tests_executing(imp)
    return mt
BASE_MT = {rel: base_module_tests(rel) for rel in files}

def _module_level_imports(tree, rel):
    pkg = modname(rel).split(".")
    if not rel.endswith("__init__.py"):
        pkg = pkg[:-1]
    found = set()
    def walk(node):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(ch, ast.Import):
                for a in ch.names:
                    found.add(a.name)
                    parts = a.name.split(".")
                    for k in range(1, len(parts)):
                        found.add(".".join(parts[:k]))
            elif isinstance(ch, ast.ImportFrom):
                if ch.level:
                    b = ".".join(pkg[: len(pkg) - ch.level + 1])
                    mname = b + ("." + ch.module if ch.module else "")
                else:
                    mname = ch.module or ""
                found.add(mname)
                parts = mname.split(".")
                for k in range(1, len(parts)):
                    found.add(".".join(parts[:k]))
                for a in ch.names:
                    found.add(mname + "." + a.name)
            walk(ch)
    walk(tree)
    return found

ML_IMPORTERS = collections.defaultdict(set)
for _rel, _src in files.items():
    try:
        _tree = ast.parse(_src)
    except SyntaxError:
        continue
    for _m in _module_level_imports(_tree, _rel):
        _f = M2F.get(_m)
        if _f and _f != _rel:
            ML_IMPORTERS[_f].add(_rel)

_FAIL_CACHE = {}
def _lazy_dialect_tests():
    """Tests whose own body imports a dialect lazily (function-level
    `from sqlglot.dialects import X` or `sqlglot.dialects.X`): after another
    test cached the class, coverage cannot see that they depend on the dialect
    module importing, so they count as importers of every dialect module."""
    out = set()
    for rel in files:
        if not rel.startswith("tests/"):
            continue
        tree = _src_tree(rel)
        if tree is None:
            continue
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            lazy = any(
                (isinstance(n, ast.ImportFrom) and n.module == "sqlglot.dialects")
                or (isinstance(n, ast.Attribute) and isinstance(n.value, ast.Attribute)
                    and n.value.attr == "dialects" and isinstance(n.value.value, ast.Name)
                    and n.value.value.id == "sqlglot")
                for n in ast.walk(fn))
            if lazy:
                out |= {i for tid, i in index.items()
                        if tid.split("::")[0] == rel and tid.split("::")[-1].split("[")[0] == fn.name}
    return out
LAZY_DIALECT_TESTS = _lazy_dialect_tests()

def tests_if_import_fails(modname_):
    """A module whose import breaks takes down every test that uses it,
    including every module (and test file) that imports it at module level,
    transitively."""
    f = M2F.get(modname_)
    if not f:
        return set()
    if f in _FAIL_CACHE:
        return _FAIL_CACHE[f]
    out = set(BASE_MT.get(f, set()))
    if f.startswith("sqlglot/dialects/"):
        out |= LAZY_DIALECT_TESTS
    seen, todo = {f}, [f]
    while todo:
        g = todo.pop()
        for h in ML_IMPORTERS.get(g, ()):
            if h in seen:
                continue
            seen.add(h); todo.append(h)
            out |= instantiators_of_module(h)
            if h.startswith("tests/"):
                out |= members.get(h, set())
    _FAIL_CACHE[f] = out
    return out

out_files = {}
for rel, src in files.items():
    by_line = lines_by_file.get(rel, {})
    execd = tests_executing(rel)
    module_tests = set(BASE_MT[rel])
    if rel.startswith("tests/"):
        module_tests |= members.get(rel, set())
    elif module_ctx_lines.get(rel):
        # imported at collection time: a fresh `python -c "import sqlglot"`
        # child (test_lazy_load) runs this module-level code untraced
        module_tests |= SUBPROCESS_TESTS
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
        for mod in import_inst.get(m + ":" + q, ()):
            s |= tests_if_import_fails(mod)
        if s and rel.startswith("sqlglot/"):
            # class-level edits can break a dialect module's import through
            # subclass creation (__init_subclass__), which lazy importers hit
            s |= LAZY_DIALECT_TESTS
        class_tests[q] = s
    sets, set_ids = [], {}
    def sid(s):
        key = tuple(sorted(s))
        if key not in set_ids:
            set_ids[key] = len(sets); sets.append(list(key))
        return set_ids[key]
    attrs = None
    dk = {}
    if have_attrs and rel.startswith("sqlglot/"):
        attrs = {}
        dt_names = _definition_time_names(ast.parse(src))
        bare_names = _bare_definition_names(ast.parse(src))
        for q in classes.get(rel, {}):
            per = {}
            prefix = m + ":" + q + "."
            for key in [k for k in instrumented if k.startswith(prefix)]:
                name = key[len(prefix):]
                tr = None if "." in name else tok_flag_readers(key, name)
                if tr is not None:
                    per[name] = sid(tr)
                    continue
                if "." in name or key in import_keys or name in STATIC_IMPORT_NAMES or name in dt_names:
                    continue
                per[name] = sid(attr_reads.get(key, set()))
            # class-body dict literals wrapped before class creation: per-key
            # value readers (class-creation reads are recorded as import reads)
            for key in [k for k in dictkeys if k.startswith(prefix)]:
                name = key[len(prefix):]
                if "." in name or not _dict_base_ok(key):
                    continue
                by = {}
                for t in set(dictkeys[key]):
                    r = key_readers(key, t)
                    by[t] = None if r is None else sid(r)
                if all(v is None for v in by.values()):
                    continue
                dk.setdefault(q, {})[name] = {
                    "keys": dictkeys[key],
                    "all": sid(set()),  # whole-dict readers are folded into "by"
                    "by": by,
                }
            if per:
                attrs[q] = per
    out_files[rel] = {
        "import_lines": sorted(module_ctx_lines.get(rel, ())),
        "import_tests": {
            str(l): sid(set().union(*(tests_if_import_fails(mm) for mm in mods)))
            for l, mods in sorted(import_mods_by_line.get(rel, {}).items())
        },
        "attrs": attrs,
        "dictkeys": dk,
        "cmp": None if rel not in cmp_files else {
            n[len(rel) + 1:]: (None if n in cmp_import else sid(cmp_tests.get(n, set())))
            for n in set(cmp_tests) | cmp_import if n.startswith(rel + ":")
        },
        "source": src,
        "lines": {str(l): sid(ts) for l, ts in sorted(by_line.items()) if ts},
        "module": sid(module_tests),
        "classes": {q: sid(s) for q, s in class_tests.items()},
        "readers": sid(readers.get(rel, set())),
        "unstable": {str(l): sid(ts) for l, ts in sorted(unstable_lines.get(rel, {}).items())},
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
