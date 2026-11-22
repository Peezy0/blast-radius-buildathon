"""pytest plugin: per-test (per phase) line coverage and file-open evidence.

Loaded with ``-p br_collect``. Writes to $BR_OUT:
  coverage data (.coverage, contexts "<nodeid>|<phase>" and "collect")
  opens.json     {context: [repo-relative paths opened]}
  outcomes.json  {nodeid: {phase: outcome}}
"""
import json, os, sys
import coverage
import pytest

ROOT = os.path.realpath(os.environ.get("BR_ROOT", os.getcwd()))
OUT = os.environ["BR_OUT"]
_ctx = ["collect"]
_opens = {}
_outcomes = {}

def _audit(event, args):
    _d = globals().get("_import_depth")
    if _d and _d[0]:
        return  # reading module source while importing is not a data dependency
    if event == "open" and args and isinstance(args[0], (str, bytes, os.PathLike)):
        try:
            p = os.path.realpath(os.fsdecode(args[0]))
        except Exception:
            return
        if p.startswith(ROOT + os.sep) and not p.endswith((".pyc",)):
            _opens.setdefault(_ctx[0], set()).add(os.path.relpath(p, ROOT))
    elif event in ("os.listdir", "os.scandir") and args and args[0] is not None:
        try:
            p = os.path.realpath(os.fsdecode(args[0]))
        except Exception:
            return
        if p.startswith(ROOT + os.sep) or p == ROOT:
            _opens.setdefault(_ctx[0], set()).add(os.path.relpath(p, ROOT) + "/")

_MAIN = os.environ.setdefault("BR_MAIN_PID", str(os.getpid())) == str(os.getpid())
_cov = coverage.Coverage(
    data_file=os.path.join(OUT, ".coverage"),
    data_suffix=not _MAIN,  # worker processes write .coverage.<host>.<pid>.<rand>
    source=[os.path.join(ROOT, "sqlglot"), os.path.join(ROOT, "tests")],
    context="collect",
)
_cov.start()
sys.addaudithook(_audit)

# --- class data-attribute read tracking ------------------------------------
# After a sqlglot module executes, each non-callable, non-dunder class attribute
# is replaced by a non-data descriptor that records (defining class, name) in
# the current context. Reads while any sqlglot module is executing (imports,
# class creation, metaclass/__init_subclass__ work) are recorded as import-time.
import importlib.abc, importlib.machinery, types as _t2, enum
_attr_reads = {}
_instrumented = set()
_CUR_INIT = None
_import_reads = set()
_import_depth = [0]
_CUR = [_attr_reads.setdefault("collect", set())]
# comparison infection evidence: node ids where `<`/`<=` (or `>`/`>=`) would
# differ (operands equal) or where `is` and `==` would differ, per context
_cmp_obs = {}
_CMPCUR = [_cmp_obs.setdefault("collect", set())]
_cmp_files = set()

class _AttrRec:
    __slots__ = ("key", "value")
    def __init__(self, key, value):
        self.key = key; self.value = value
    def __get__(self, obj, owner=None):
        if _import_depth[0]:
            _import_reads.add(self.key)
        else:
            _CUR[0].add(self.key)
        return self.value

_dict_keys = {}   # attr key -> runtime key tokens, in insertion order

def _key_token(k):
    """Stable name for a dict key, matched against the source by covselect."""
    if isinstance(k, enum.Enum):
        return k.name  # matched against the last name in the source key
    if isinstance(k, type):
        return k.__name__
    if k is None or type(k) in (str, int, float, bool, bytes):
        return repr(k)
    if type(k) is tuple:
        parts = [_key_token(x) for x in k]
        return None if None in parts else "(" + ", ".join(parts) + ("," if len(parts) == 1 else "") + ")"
    return None

_import_sites = set()   # (read key, "file:line" of the reading code) at import
_suppress = [False]

def _site():
    f = sys._getframe(1)
    while f is not None and f.f_code.co_filename == __file__:
        f = f.f_back
    return "%s:%d" % (f.f_code.co_filename, f.f_lineno) if f is not None else "?"

class _RecDict(dict):
    """Class-attribute dict that records which key's value each context reads.
    Value reads of key K go to '<attr>\x00<token>'; whole-dict reads (items,
    values, copies, comparisons, repr, pickling) go to '<attr>\x00*'. Key-only
    operations (in, len, iteration over keys) record nothing: covselect only
    uses these records when an edit keeps the dict's keys."""
    __slots__ = ("_k", "_tm")
    def _all(self):
        if _suppress[0]:
            return
        if _import_depth[0]:
            _import_reads.add(self._k + "\x00*")
            _import_sites.add((self._k + "\x00*", _site()))
        else:
            _CUR[0].add(self._k + "\x00*")
    def _one(self, k):
        try:
            t = self._tm.get(k)
        except TypeError:
            return
        if t is None:
            if dict.__contains__(self, k):
                self._all()  # key added at runtime: no source mapping
            return
        if _suppress[0]:
            return
        if _import_depth[0]:
            _import_reads.add(t)
            _import_sites.add((t, _site()))
        else:
            _CUR[0].add(t)
    def __getitem__(self, k):
        self._one(k); return dict.__getitem__(self, k)
    def get(self, k, *a):
        self._one(k); return dict.get(self, k, *a)
    def setdefault(self, k, *a):
        self._one(k); return dict.setdefault(self, k, *a)
    def pop(self, k, *a):
        self._one(k); return dict.pop(self, k, *a)
    def __iter__(self):
        # a Python-level __iter__ also makes dict(d), {**d}, f(**d) and update(d)
        # go through keys() + __getitem__ instead of copying storage directly
        return dict.__iter__(self)
    def items(self):
        self._all(); return dict.items(self)
    def values(self):
        self._all(); return dict.values(self)
    def popitem(self):
        self._all(); return dict.popitem(self)
    def copy(self):
        self._all(); return dict(dict.items(self))
    def __eq__(self, o):
        self._all(); return dict.__eq__(self, o)
    def __ne__(self, o):
        self._all(); return dict.__ne__(self, o)
    __hash__ = None
    def __repr__(self):
        self._all(); return dict.__repr__(self)
    def __or__(self, o):
        self._all(); return dict.__or__(self, o)
    def __ror__(self, o):
        self._all(); return dict.__ror__(self, o)
    def __reduce_ex__(self, proto):
        self._all(); return (dict, (dict(dict.items(self)),))
    def __deepcopy__(self, memo):
        import copy as _copy
        self._all(); return _copy.deepcopy(dict(dict.items(self)), memo)
    def __copy__(self):
        self._all(); return dict(dict.items(self))

def _rec_dict(key, v):
    toks = [_key_token(k) for k in v]
    d = _RecDict(v)
    d._k = key
    d._tm = {k: key + "\x00" + t for k, t in zip(v, toks) if t is not None}
    _dict_keys[key] = toks
    return d

# Class-body dict literals are wrapped before the metaclass and
# __init_subclass__ run, so class-creation reads are recorded too. Only dicts
# referenced from the class namespace alone are wrapped (no aliasing changes).
import builtins as _bi
_orig_build_class = _bi.__build_class__

def _calc_meta(meta, bases):
    winner = meta
    for b in bases:
        bt = type(b)
        if issubclass(winner, bt):
            continue
        if issubclass(bt, winner):
            winner = bt; continue
        return None
    return winner

def _probe_refs(ns):
    for k, v in list(ns.items()):
        return sys.getrefcount(v)
_BASE_REFS = _probe_refs({"a": {}})

def _wrap_ns(ns, mod):
    qual = ns.get("__qualname__")
    if not isinstance(qual, str):
        return
    for k, v in list(ns.items()):
        if type(v) is dict and not k.startswith("__") and sys.getrefcount(v) <= _BASE_REFS:
            ns[k] = _rec_dict(mod + ":" + qual + "." + k, v)

class _MetaShim:
    def __init__(self, meta, mod):
        self.meta = meta; self.mod = mod
    def __prepare__(self, name, bases, **kw):
        return self.meta.__prepare__(name, bases, **kw)
    def __call__(self, name, bases, ns, **kw):
        _wrap_ns(ns, self.mod)
        return self.meta(name, bases, ns, **kw)

def _build_class(func, name, *bases, **kw):
    mod = getattr(func, "__globals__", {}).get("__name__", "")
    if not _import_depth[0] or not (mod == "sqlglot" or mod.startswith("sqlglot.")):
        return _orig_build_class(func, name, *bases, **kw)
    meta = kw.pop("metaclass", type)
    try:
        rb = _t2.resolve_bases(bases)
        real = _calc_meta(meta, rb) if isinstance(meta, type) else None
    except Exception:
        real = None
    if real is None or issubclass(real, enum.EnumMeta):
        return _orig_build_class(func, name, *bases, metaclass=meta, **kw)
    return _orig_build_class(func, name, *bases, metaclass=_MetaShim(real, mod), **kw)

_bi.__build_class__ = _build_class

class _DispatchRec(dict):
    """Generator dispatch table (a copy of cls.TRANSFORMS plus *_sql methods):
    a lookup that returns a TRANSFORMS value is recorded as a read of that key."""
    __slots__ = ("_t",)
    def _rec(self, k):
        try:
            v = dict.get(self, k, _MISSING)
            if v is not _MISSING and dict.get(self._t, k, _MISSING) is v:
                self._t._one(k)
        except TypeError:
            pass
    def get(self, k, *a):
        self._rec(k); return dict.get(self, k, *a)
    def __getitem__(self, k):
        self._rec(k); return dict.__getitem__(self, k)
    def __iter__(self):
        return dict.__iter__(self)
    def items(self):
        self._t._all(); return dict.items(self)
    def values(self):
        self._t._all(); return dict.values(self)
    def copy(self):
        self._t._all(); return dict(dict.items(self))

_MISSING = object()

def _patch_dispatch(mod):
    orig = getattr(mod, "_build_dispatch", None)
    if orig is None:
        return
    def _build_dispatch(cls):
        _suppress[0] = True
        try:
            d = orig(cls)
        finally:
            _suppress[0] = False
        t = cls.__dict__.get("TRANSFORMS")
        t = getattr(t, "value", None) if isinstance(t, _AttrRec) else t
        if not isinstance(t, _RecDict):
            # inherited TRANSFORMS: find the defining class's dict
            for c in cls.__mro__:
                r = c.__dict__.get("TRANSFORMS")
                if r is not None:
                    t = getattr(r, "value", r)
                    break
        if isinstance(t, _RecDict) and type(d) is dict:
            out = _DispatchRec(d)
            out._t = t
            return out
        if isinstance(t, _RecDict):
            t._all()
        return d
    mod._build_dispatch = _build_dispatch

_SKIP_TYPES = (_t2.FunctionType, classmethod, staticmethod, property, type,
               _t2.BuiltinFunctionType, _t2.MethodType)

def _instrument_module(mod):
    name = mod.__name__
    stack = [v for v in list(vars(mod).values()) if isinstance(v, type)]
    seen = set()
    while stack:
        c = stack.pop()
        if id(c) in seen or c.__module__ != name or issubclass(c, enum.Enum):
            continue
        seen.add(id(c))
        for k, v in list(vars(c).items()):
            if isinstance(v, type):
                stack.append(v); continue
            if k.startswith("__") or isinstance(v, (_SKIP_TYPES, _AttrRec)) or callable(v) \
                    or hasattr(type(v), "__get__"):
                continue
            try:
                key = name + ":" + c.__qualname__ + "." + k
                setattr(c, k, _AttrRec(key, v))
                _instrumented.add(key)
            except (TypeError, AttributeError):
                pass

_import_ctx_stack = []

class _Loader(importlib.abc.Loader):
    def __init__(self, inner):
        self.inner = inner
    def create_module(self, spec):
        return self.inner.create_module(spec)
    def exec_module(self, module):
        _import_depth[0] += 1
        # code run while a sqlglot module executes is import-time code: its
        # effects persist for every later test, so record it separately
        _import_ctx_stack.append("import:" + module.__name__)
        _cov.switch_context(_import_ctx_stack[-1])
        try:
            code = _instrumented_code(self.inner, module)
            if code is None:
                self.inner.exec_module(module)
            else:
                exec(code, module.__dict__)
        finally:
            _import_depth[0] -= 1
            _import_ctx_stack.pop()
            _cov.switch_context(_import_ctx_stack[-1] if _import_ctx_stack else _ctx[0])
        _instrument_module(module)
        if module.__name__ == "sqlglot.generator":
            _patch_dispatch(module)
    def __getattr__(self, item):
        return getattr(self.inner, item)

def _cmp_mark(nid):
    (_import_cmp if _import_depth[0] else _CMPCUR[0]).add(nid)

_import_cmp = set()

_SIMPLE = (int, float, str, bytes, bool, type(None), complex)

def _simple(x):
    """Values whose == is side-effect free and cheap to evaluate here."""
    t = type(x)
    if t in _SIMPLE or isinstance(x, enum.Enum):
        return True
    if t is tuple or t is frozenset:
        return all(_simple(y) for y in x)
    if isinstance(x, type):
        return type(x) is type or type(x).__eq__ is type.__eq__
    return False

def _eq_known(a, b):
    """a == b when it can be evaluated safely, else None (unknown)."""
    if _simple(a) and _simple(b):
        try:
            return bool(a == b)
        except Exception:
            return None
    return None

def _boundary(nid, a, b):
    if _eq_known(a, b) is not False:
        _cmp_mark(nid)  # equal, or not safely decidable

def _brc_lt(nid, a, b):
    r = a < b
    if type(r) is not bool:
        _cmp_mark(nid)  # overloaded (e.g. builds an expression): could differ
    elif not r:
        _boundary(nid, a, b)
    return r

def _brc_le(nid, a, b):
    r = a <= b
    if type(r) is not bool:
        _cmp_mark(nid)  # overloaded (e.g. builds an expression): could differ
    elif r:
        _boundary(nid, a, b)
    return r

def _brc_gt(nid, a, b):
    r = a > b
    if type(r) is not bool:
        _cmp_mark(nid)  # overloaded (e.g. builds an expression): could differ
    elif not r:
        _boundary(nid, a, b)
    return r

def _brc_ge(nid, a, b):
    r = a >= b
    if type(r) is not bool:
        _cmp_mark(nid)  # overloaded (e.g. builds an expression): could differ
    elif r:
        _boundary(nid, a, b)
    return r

def _brc_eq(nid, a, b):
    r = a == b
    if a is b:
        if r is not True:
            _cmp_mark(nid)  # e.g. NaN, or a non-bool result
    elif r is not False:
        _cmp_mark(nid)
    return r

def _brc_ne(nid, a, b):
    r = a != b
    if a is b:
        if r is not False:
            _cmp_mark(nid)
    elif r is not True:
        _cmp_mark(nid)
    return r

def _brc_is(nid, a, b):
    # `a is b` vs `a == b`: differ when identical but unequal, or equal but
    # not identical; == is only evaluated for simple values (else: recorded)
    r = a is b
    e = _eq_known(a, b)
    if e is None or e != r:
        _cmp_mark(nid)
    return r

def _brc_isnot(nid, a, b):
    r = a is not b
    e = _eq_known(a, b)
    if e is None or e == r:
        _cmp_mark(nid)
    return r

def _brc_ba(nid, isand, a):
    # first operand of `a and b` / `a or b` used as a condition: when it
    # short-circuits, the swapped operator would evaluate b (unknown here)
    if (not a) if isand else a:
        _cmp_mark(nid)
    return a

def _brc_bb(nid, isand, b):
    # reached when a was truthy (and) / falsy (or): the swapped operator gives
    # True (and->or) / False (or->and); it differs when b is falsy / truthy
    if (not b) if isand else b:
        _cmp_mark(nid)
    return b

def _instrumented_code(inner, module):
    """Compile a sqlglot module with each single comparison routed through a
    recorder (same line/column positions, so coverage is unaffected)."""
    import ast
    try:
        name = module.__name__
        path = inner.get_filename(name)
        src = inner.get_source(name)
    except Exception:
        return None
    if not path or not path.endswith(".py") or src is None:
        return None
    rel = os.path.relpath(os.path.realpath(path), ROOT)
    ops = {ast.Lt: "_brc_lt", ast.LtE: "_brc_le", ast.Gt: "_brc_gt", ast.GtE: "_brc_ge",
           ast.Eq: "_brc_eq", ast.NotEq: "_brc_ne", ast.Is: "_brc_is", ast.IsNot: "_brc_isnot"}
    def cond(test):
        if isinstance(test, ast.BoolOp) and len(test.values) == 2:
            isand = isinstance(test.op, ast.And)
            nid = "%s:B:%d:%d" % (rel, test.lineno, test.col_offset)
            a, b = test.values
            test.values = [
                ast.copy_location(ast.Call(func=ast.Name(id="_brc_ba", ctx=ast.Load()),
                                           args=[ast.Constant(nid), ast.Constant(isand), a], keywords=[]), a),
                ast.copy_location(ast.Call(func=ast.Name(id="_brc_bb", ctx=ast.Load()),
                                           args=[ast.Constant(nid), ast.Constant(isand), b], keywords=[]), b),
            ]
        return test
    class T(ast.NodeTransformer):
        def visit_If(self, node):
            self.generic_visit(node); node.test = cond(node.test); return node
        def visit_While(self, node):
            self.generic_visit(node); node.test = cond(node.test); return node
        def visit_IfExp(self, node):
            self.generic_visit(node); node.test = cond(node.test); return node
        def visit_Assert(self, node):
            self.generic_visit(node); node.test = cond(node.test); return node
        def visit_comprehension(self, node):
            self.generic_visit(node); node.ifs = [cond(t) for t in node.ifs]; return node
        def visit_UnaryOp(self, node):
            self.generic_visit(node)
            if isinstance(node.op, ast.Not):
                node.operand = cond(node.operand)
            return node
        def visit_Compare(self, node):
            self.generic_visit(node)
            if len(node.ops) != 1 or type(node.ops[0]) not in ops:
                return node
            right = node.comparators[0]
            if isinstance(node.ops[0], (ast.Is, ast.IsNot)) and isinstance(right, ast.Constant) and right.value is None:
                return node  # `is None` vs `== None` is handled statically
            nid = "%s:%d:%d" % (rel, node.lineno, node.col_offset)
            call = ast.Call(func=ast.Name(id=ops[type(node.ops[0])], ctx=ast.Load()),
                            args=[ast.Constant(nid), node.left, right], keywords=[])
            return ast.copy_location(call, node)
    try:
        tree = T().visit(ast.parse(src, path))
        ast.fix_missing_locations(tree)
        code = compile(tree, path, "exec", dont_inherit=True)
    except Exception:
        return None
    g = module.__dict__
    for fname in list(ops.values()) + ["_brc_ba", "_brc_bb"]:
        g[fname] = globals()[fname]
    _cmp_files.add(rel)
    return code

class _Finder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path, target=None):
        if not (fullname == "sqlglot" or fullname.startswith("sqlglot.")):
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec is not None and spec.loader is not None and not isinstance(spec.loader, _Loader):
            spec.loader = _Loader(spec.loader)
        return spec

if os.environ.get("BR_ATTRS", "1") == "1":
    sys.meta_path.insert(0, _Finder())

_types = {}
_wrapped_mods = set()
import enum, functools, types as _pytypes

def _make_wrapper(orig):
    @functools.wraps(orig)
    def __init__(self, *a, **k):
        key = _import_ctx_stack[-1] if _import_ctx_stack else _ctx[0]
        _types.setdefault(key, set()).add(type(self))
        return orig(self, *a, **k)
    __init__.__br_wrapped__ = True
    return __init__

def _wrap_new_modules():
    for name, mod in list(sys.modules.items()):
        if name in _wrapped_mods or not (name == "sqlglot" or name.startswith("sqlglot.")):
            continue
        _wrapped_mods.add(name)
        for obj in list(vars(mod).values()):
            stack = [obj]
            while stack:
                c = stack.pop()
                if not isinstance(c, type) or c.__module__ != name or issubclass(c, enum.Enum):
                    continue
                stack.extend(v for v in vars(c).values() if isinstance(v, type))
                init = c.__dict__.get("__init__")
                if isinstance(init, _pytypes.FunctionType) and not getattr(init, "__br_wrapped__", False):
                    try:
                        setattr(c, "__init__", _make_wrapper(init))
                    except (TypeError, AttributeError):
                        pass

def _switch(name):
    _wrap_new_modules()
    gen = sys.modules.get("sqlglot.generator")
    if gen is not None and isinstance(getattr(gen, "_DISPATCH_CACHE", None), dict):
        # per-class dispatch cache: clear so every test re-reads TRANSFORMS
        gen._DISPATCH_CACHE.clear()
    _CUR[0] = _attr_reads.setdefault(name, set())
    _CMPCUR[0] = _cmp_obs.setdefault(name, set())
    _ctx[0] = name
    _cov.switch_context(name)

def _flush_worker():
    """Persist evidence recorded inside a ProcessPoolExecutor worker."""
    _cov.save()
    tag = "%d" % os.getpid()
    with open(os.path.join(OUT, "worker-opens-%s.json" % tag), "w") as f:
        json.dump({k: sorted(v) for k, v in _opens.items()}, f)
    with open(os.path.join(OUT, "worker-types-%s.json" % tag), "w") as f:
        json.dump(_types_out(), f)
    with open(os.path.join(OUT, "worker-cmp-%s.json" % tag), "w") as f:
        json.dump(_cmp_out(), f)
    with open(os.path.join(OUT, "worker-attrs-%s.json" % tag), "w") as f:
        json.dump({"reads": {k: sorted(v) for k, v in _attr_reads.items()}, "import": sorted(_import_reads), "instrumented": sorted(_instrumented), "dictkeys": _dict_keys, "import_sites": sorted(_import_sites)}, f)

def _run_in_ctx(ctx, fn, args, kwargs):
    """Executed in a worker: attribute the task to the test that submitted it."""
    _switch(ctx)
    try:
        return fn(*args, **kwargs)
    finally:
        _wrap_new_modules()
        _flush_worker()

import concurrent.futures as _cf
_orig_submit = _cf.ProcessPoolExecutor.submit
_orig_ppe_init = _cf.ProcessPoolExecutor.__init__

def _submit(self, fn, /, *args, **kwargs):
    return _orig_submit(self, _run_in_ctx, _ctx[0], fn, args, kwargs)

def _ppe_init(self, *args, **kwargs):
    init = kwargs.get("initializer")
    if init is not None:
        kwargs["initargs"] = (_ctx[0], init, tuple(kwargs.get("initargs", ())), {})
        kwargs["initializer"] = _run_in_ctx
    return _orig_ppe_init(self, *args, **kwargs)

if _MAIN:
    _cf.ProcessPoolExecutor.submit = _submit
    _cf.ProcessPoolExecutor.__init__ = _ppe_init

@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    if isinstance(collector, pytest.Module):
        _switch("collect:" + collector.nodeid)
        yield
        _switch("collect")
    else:
        yield

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    _switch(item.nodeid + "|setup"); yield

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    _switch(item.nodeid + "|call"); yield

@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item, nextitem):
    _switch(item.nodeid + "|teardown"); yield
    _switch("between")

def pytest_runtest_logreport(report):
    d = _outcomes.setdefault(report.nodeid, {})
    key = report.when
    # subtests produce extra reports; keep worst outcome per phase
    prev = d.get(key)
    out = report.outcome
    if getattr(report, "context", None) is not None or type(report).__name__ == "SubtestReport":
        key = report.when + ":subtest"; prev = d.get(key)
    if prev in (None, "passed", "skipped") or out == "failed":
        d[key] = out

def pytest_collectreport(report):
    if report.failed:
        _outcomes.setdefault("<collect>" + report.nodeid, {})["collect"] = "failed"

def pytest_sessionfinish(session, exitstatus):
    _cov.stop(); _cov.save()
    with open(os.path.join(OUT, "opens.json"), "w") as f:
        json.dump({k: sorted(v) for k, v in _opens.items()}, f)
    with open(os.path.join(OUT, "types.json"), "w") as f:
        json.dump(_types_out(), f)
    with open(os.path.join(OUT, "outcomes.json"), "w") as f:
        json.dump(_outcomes, f)
    with open(os.path.join(OUT, "cmp.json"), "w") as f:
        json.dump(_cmp_out(), f)
    with open(os.path.join(OUT, "attrs.json"), "w") as f:
        json.dump({"reads": {k: sorted(v) for k, v in _attr_reads.items()}, "import": sorted(_import_reads), "instrumented": sorted(_instrumented), "dictkeys": _dict_keys, "import_sites": sorted(_import_sites)}, f)

def _cmp_out():
    return {"obs": {k: sorted(v) for k, v in _cmp_obs.items() if v},
            "import": sorted(_import_cmp), "files": sorted(_cmp_files)}

def _types_out():
    out = {}
    for k, v in _types.items():
        names = set()
        for t in v:
            for b in t.__mro__:
                m = getattr(b, "__module__", "")
                if m == "sqlglot" or m.startswith("sqlglot."):
                    names.add(m + ":" + b.__qualname__)
        out[k] = sorted(names)
    return out
