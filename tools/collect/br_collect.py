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
import importlib.abc, importlib.machinery, types as _t2
_attr_reads = {}
_instrumented = set()
_CUR_INIT = None
_import_reads = set()
_import_depth = [0]
_CUR = [_attr_reads.setdefault("collect", set())]

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

class _Loader(importlib.abc.Loader):
    def __init__(self, inner):
        self.inner = inner
    def create_module(self, spec):
        return self.inner.create_module(spec)
    def exec_module(self, module):
        _import_depth[0] += 1
        try:
            self.inner.exec_module(module)
        finally:
            _import_depth[0] -= 1
        _instrument_module(module)
    def __getattr__(self, item):
        return getattr(self.inner, item)

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
        _types.setdefault(_ctx[0], set()).add(type(self))
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
    with open(os.path.join(OUT, "worker-attrs-%s.json" % tag), "w") as f:
        json.dump({"reads": {k: sorted(v) for k, v in _attr_reads.items()}, "import": sorted(_import_reads), "instrumented": sorted(_instrumented)}, f)

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
    with open(os.path.join(OUT, "attrs.json"), "w") as f:
        json.dump({"reads": {k: sorted(v) for k, v in _attr_reads.items()}, "import": sorted(_import_reads), "instrumented": sorted(_instrumented)}, f)

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
