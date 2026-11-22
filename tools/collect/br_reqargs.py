"""pytest plugin (no coverage): required-argument evidence for arg_types edits.

An Expression class's arg_types values are read only when the class is created
(`required_args = {k for k, v in arg_types.items() if v}`); required_args is then
read only by Expression.error_messages, which reports a key only when that key's
argument is missing. So a value edit of key k in C.arg_types can change a result
only where error_messages runs on an instance whose class takes its arg_types
from C (directly, or via a copy) while argument k is missing.

Writes $BR_REQARGS_OUT(.<worker>) with
  missing: {context: [[class key, arg key], ...]}  (every arg_types key that was
           missing, required or not; contexts "<nodeid>|<phase>", or "collect")
  owners:  {class key: dict key of the arg_types it uses ("mod:Qual.arg_types")}
"""
import json, os, sys

_ctx = ["collect"]
_missing = {}
_wrapped = [False]


def _key(cls):
    return "%s:%s" % (cls.__module__, cls.__qualname__)


def _wrap():
    if _wrapped[0]:
        return
    from sqlglot.expressions import core
    orig = core.Expression.error_messages
    if "error_messages" in {n for c in core.Expression.__subclasses__() for n in vars(c)}:
        raise RuntimeError("error_messages overridden: evidence would be incomplete")

    def error_messages(self, args=None):
        cls = type(self)
        rec = _missing.setdefault(_ctx[0], set())
        get = self.args.get
        for k in dict.keys(cls.arg_types):
            v = get(k)
            if v is None or (isinstance(v, list) and not v):
                rec.add((_key(cls), k))
        return orig(self, args)

    error_messages.__wrapped__ = orig
    core.Expression.error_messages = error_messages
    _wrapped[0] = True


def _setup():
    if _wrapped[0]:
        return
    _wrap()
    # import every sqlglot module now: calls made while modules import (which
    # would fail an import for every later user) land in context "import"
    import importlib, pkgutil, sqlglot
    prev, _ctx[0] = _ctx[0], "import"
    for m in pkgutil.walk_packages(sqlglot.__path__, "sqlglot."):
        try:
            importlib.import_module(m.name)
        except Exception:
            pass
    _ctx[0] = prev


def pytest_configure(config):
    _setup()


# ProcessPoolExecutor workers (spawned interpreters): attribute each task to
# the test that submitted it and persist the evidence from the worker.
def _flush(tag):
    with open(os.environ["BR_REQARGS_OUT"] + "." + tag, "w") as f:
        json.dump({"missing": {c: sorted(map(list, v)) for c, v in _missing.items()},
                   "owners": _owners()}, f)


def _run_in_ctx(ctx, fn, args, kwargs):
    _ctx[0] = "import"
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


def _phase(item, phase):
    _ctx[0] = item.nodeid + "|" + phase


import pytest


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_setup(item):
    _phase(item, "setup"); yield; _ctx[0] = "collect"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    _phase(item, "call"); yield; _ctx[0] = "collect"


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_teardown(item):
    _phase(item, "teardown"); yield; _ctx[0] = "collect"


@pytest.hookimpl(hookwrapper=True)
def pytest_make_collect_report(collector):
    # importing a test module: a failure there fails every test in that file
    prev = _ctx[0]
    if isinstance(collector, pytest.Module):
        _ctx[0] = "collect:" + collector.nodeid.split("::")[0]
    yield
    _ctx[0] = prev


def _owners():
    from sqlglot.expressions import core
    out, seen, stack = {}, set(), [core.Expression]
    while stack:
        c = stack.pop()
        if c in seen:
            continue
        seen.add(c)
        stack.extend(c.__subclasses__())
        if not c.__module__.startswith("sqlglot"):
            continue
        owner = next((b for b in c.__mro__ if "arg_types" in vars(b)), None)
        out[_key(c)] = None if owner is None else _key(owner) + ".arg_types"
    return out


def pytest_sessionfinish(session, exitstatus):
    _flush(getattr(session.config, "workerinput", {}).get("workerid", "main"))
