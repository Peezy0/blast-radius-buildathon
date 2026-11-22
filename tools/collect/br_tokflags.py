"""pytest plugin (no coverage): evidence for scalar tokenizer flag edits.

Tokenizer._init_core copies scalar class flags (`self.F`) into TokenizerCore
fields when a tokenizer is created, so every tokenizing test "reads" every
flag. The value only matters where a core field is read while scanning, or
where the flag is read directly elsewhere. Records, per context, the flag
definitions ("mod:Qual.F", resolved through the tokenizer class's MRO) whose
value was used either way. Contexts: "<nodeid>|<phase>", "collect:<file>",
"import" (module imports: callers fall back) and "between".
Writes $BR_TOKFLAGS_OUT.<worker> = {"uses": {ctx: [key, ...]}, "flags": {F: field}}
"""
import ast, inspect, json, os, sys, textwrap
import pytest

_ctx = ["collect"]
_uses = {}
_done = [False]
_FLAGS = {}      # flag name -> core field
_origin = {}     # id(core) -> (core, tokenizer class)
_INIT_CODE = [None]


def _key(cls, f):
    for k in cls.__mro__:
        if f in vars(k):
            return "%s:%s.%s" % (k.__module__, k.__qualname__, f)
    return None


def _rec(key):
    _uses.setdefault(_ctx[0], set()).add(key)


class _FlagAttr:
    """Class flag replaced by a recording non-data descriptor."""
    __slots__ = ("key", "value")
    def __init__(self, key, value):
        self.key = key; self.value = value
    def __get__(self, obj, owner=None):
        if sys._getframe(1).f_code is not _INIT_CODE[0]:
            _rec(self.key)
        return self.value


def _wrap_class(cls):
    for f in _FLAGS:
        v = vars(cls).get(f, None)
        if f in vars(cls) and not isinstance(v, _FlagAttr):
            setattr(cls, f, _FlagAttr("%s:%s.%s" % (cls.__module__, cls.__qualname__, f), v))


def _setup():
    if _done[0]:
        return
    _done[0] = True
    prev, _ctx[0] = _ctx[0], "import"
    from sqlglot import tokens, tokenizer_core
    Tok, Core = tokens.Tokenizer, tokenizer_core.TokenizerCore
    src = textwrap.dedent(inspect.getsource(Tok._init_core))
    call = next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.Call))
    for kw in call.keywords:
        v = kw.value
        if (isinstance(v, ast.Attribute) and isinstance(v.value, ast.Name) and v.value.id == "self"
                and type(getattr(Tok, v.attr, None)) in (bool, int, str, type(None))):
            _FLAGS[v.attr] = kw.arg
    orig_init = Tok._init_core
    def _init_core(self):
        core = orig_init(self)
        _origin[id(core)] = (core, type(self))
        return core
    _INIT_CODE[0] = orig_init.__code__
    Tok._init_core = _init_core
    for f, field in _FLAGS.items():
        md = Core.__dict__[field]
        def get(obj, md=md, f=f):
            o = _origin.get(id(obj))
            _rec(_key(o[1], f) if o is not None and o[0] is obj else "?:" + f)
            return md.__get__(obj, Core)
        def set_(obj, v, md=md):
            md.__set__(obj, v)
        setattr(Core, field, property(get, set_))
    # every Tokenizer class: flags it defines become recording descriptors
    base = tokens._TokenizerBase
    orig_sub = base.__dict__["__init_subclass__"].__func__
    def __init_subclass__(cls, **kw):
        orig_sub(cls, **kw)
        _wrap_class(cls)
    base.__init_subclass__ = classmethod(__init_subclass__)
    stack = [base]
    while stack:
        c = stack.pop(); _wrap_class(c); stack.extend(c.__subclasses__())
    import importlib, pkgutil, sqlglot
    for m in pkgutil.walk_packages(sqlglot.__path__, "sqlglot."):
        try:
            importlib.import_module(m.name)
        except Exception:
            pass
    _ctx[0] = prev


def pytest_configure(config):
    _setup()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    _ctx[0] = item.nodeid + "|setup"; yield; _ctx[0] = "between"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    _ctx[0] = item.nodeid + "|call"; yield; _ctx[0] = "between"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    _ctx[0] = item.nodeid + "|teardown"; yield; _ctx[0] = "between"


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    prev = _ctx[0]
    if isinstance(collector, pytest.Module):
        _ctx[0] = "collect:" + collector.nodeid.split("::")[0]
    yield
    _ctx[0] = prev


def _flush(tag):
    with open(os.environ["BR_TOKFLAGS_OUT"] + "." + tag, "w") as f:
        json.dump({"uses": {c: sorted(v) for c, v in _uses.items()}, "flags": _FLAGS}, f)


def _run_in_ctx(ctx, fn, args, kwargs):
    _setup()
    _ctx[0] = ctx
    try:
        return fn(*args, **kwargs)
    finally:
        _ctx[0] = "between"
        _flush("pid%d" % os.getpid())


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
_cf.ProcessPoolExecutor.submit = _submit
_cf.ProcessPoolExecutor.__init__ = _ppe_init


def pytest_sessionfinish(session, exitstatus):
    _flush(getattr(session.config, "workerinput", {}).get("workerid", "main"))
