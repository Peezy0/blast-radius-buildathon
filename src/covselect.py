"""Coverage-map test selection for one unified diff against the pinned sqlglot.

``select(patch_text, cmap)`` returns a set of map test indices, or ``None`` when
the change cannot be attributed and every test must run.

The map (built by tools/collect/build_map.py from running the pinned test-suite
with per-test coverage) holds, per source file: the pinned source, line -> tests
that executed it, tests affected by a module-level change, by a class-body change
(including tests that instantiated the class or a subclass), and tests that read
the file as data. Data files (fixtures) map to the tests that opened them.

Rules, per changed file:
* a statement inside a function body: tests that executed any line of that
  statement (a statement no test executes cannot change any test's behaviour);
* a pure insertion inside a function: tests that executed that function;
* class-body statements: the class's tests; module-level statements: the
  module's tests; a def/class header counts as a change to its container;
* comment/blank-only hunks change nothing; identical ASTs select only readers;
* syntax errors, deletions, renames and unknown files fall back to all tests.
"""

from __future__ import annotations

import ast
import re

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


class Unknown(Exception):
    pass


def parse_patch(text):
    """Yield (old_path, new_path, hunks); hunk = (old_start, old_count, new_start, new_count, lines)."""
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    files = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- ") and i + 1 < len(lines) and lines[i + 1].startswith("+++ "):
            old = _strip(line[4:])
            new = _strip(lines[i + 1][4:])
            i += 2
            hunks = []
            while i < len(lines) and lines[i].startswith("@@"):
                m = _HUNK.match(lines[i])
                if not m:
                    raise Unknown("bad hunk")
                os_, oc = int(m[1]), int(m[2]) if m[2] is not None else 1
                ns, nc = int(m[3]), int(m[4]) if m[4] is not None else 1
                i += 1
                body = []
                ro = rn = 0
                while ro < oc or rn < nc:
                    if i >= len(lines):
                        raise Unknown("truncated hunk")
                    l = lines[i]
                    if l.startswith("\\"):
                        i += 1
                        continue
                    k = l[:1] or " "
                    if k not in " +-":
                        raise Unknown("bad hunk line")
                    body.append((k, l[1:]))
                    ro += k != "+"
                    rn += k != "-"
                    i += 1
                while i < len(lines) and lines[i].startswith("\\"):
                    i += 1
                hunks.append((os_, oc, ns, nc, body))
            files.append((old, new, hunks))
            continue
        if line.startswith(("rename ", "copy ", "similarity ", "Binary ", "GIT binary")):
            raise Unknown("rename/binary")
        i += 1
    if not files:
        raise Unknown("no files")
    return files


def _strip(p):
    p = p.split("\t", 1)[0].strip()
    if p == "/dev/null":
        return None
    if p.startswith(("a/", "b/")):
        p = p[2:]
    return p


def apply(source, hunks):
    """Return (new_source, deleted_old_lines, inserted) where inserted maps
    new line -> old line index it was inserted before (1-based)."""
    old = source.split("\n")
    out = []
    cur = 0
    deleted = []
    added = []  # (new_line, old_line_before_which, hunk_has_deletions)
    for os_, oc, ns, nc, body in hunks:
        start = os_ - 1 if oc else os_
        if start < cur or start > len(old):
            raise Unknown("hunk order")
        out.extend(old[cur:start])
        cur = start
        has_del = any(k == "-" for k, _ in body)
        # pair runs of n deleted lines immediately followed by n added lines
        pairs = {}
        j = 0
        while j < len(body):
            if body[j][0] == "-":
                a = j
                while j < len(body) and body[j][0] == "-":
                    j += 1
                b = j
                while j < len(body) and body[j][0] == "+":
                    j += 1
                if j - b == b - a:
                    for off in range(b - a):
                        pairs[b + off] = a + off
            else:
                j += 1
        old_line_of = {}
        for pos, (k, v) in enumerate(body):
            if k != "+":
                if cur >= len(old) or old[cur] != v:
                    raise Unknown("context mismatch")
                if k == "-":
                    deleted.append(cur + 1)
                old_line_of[pos] = cur + 1
                cur += 1
            if k != "-":
                if k == "+":
                    paired = old_line_of.get(pairs[pos]) if pos in pairs else None
                    added.append((len(out) + 1, cur + 1, has_del, paired))
                out.append(v)
    out.extend(old[cur:])
    return "\n".join(out), deleted, added


def _string_spans(tree):
    spans = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.Constant, ast.JoinedStr)) and getattr(n, "end_lineno", None):
            if n.end_lineno > n.lineno:
                spans.append((n.lineno, n.end_lineno))
    return spans


def _is_noop_line(text, line, spans):
    s = text.strip()
    if s and not s.startswith("#"):
        return False
    return not any(a < line <= b or a <= line < b for a, b in spans)


class _Index:
    """Containers (def/class) and statements of one parsed module."""

    def __init__(self, tree):
        self.containers = []  # (start, end, kind, qualname, node)
        self.stmts = []  # (start, end, node, header_end)
        self._walk(tree, "", None)

    def _walk(self, node, prefix, parent_kind):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.stmt):
                start = min([ch.lineno] + [d.lineno for d in getattr(ch, "decorator_list", [])])
                body = getattr(ch, "body", None)
                header_end = (body[0].lineno - 1) if body and isinstance(body, list) and body and isinstance(body[0], ast.stmt) else ch.end_lineno
                self.stmts.append((start, ch.end_lineno, ch, header_end))
                if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    q = prefix + ch.name
                    self.containers.append((start, ch.end_lineno, "def", q, ch))
                    self._walk(ch, q + ".<locals>.", "def")
                    continue
                if isinstance(ch, ast.ClassDef):
                    q = prefix + ch.name
                    self.containers.append((start, ch.end_lineno, "class", q, ch))
                    self._walk(ch, q + ".", "class")
                    continue
            self._walk(ch, prefix, parent_kind)

    def container(self, line):
        best = None
        for c in self.containers:
            if c[0] <= line <= c[1] and (best is None or c[1] - c[0] < best[1] - best[0]):
                best = c
        return best

    def stmt(self, line):
        best = None
        for s in self.stmts:
            if s[0] <= line <= s[1] and (best is None or s[1] - s[0] < best[1] - best[0]):
                best = s
        return best


def _lines_tests(rec, a, b):
    lines, sets = rec["lines"], rec["sets"]
    out = set()
    for l in range(a, b + 1):
        k = lines.get(str(l))
        if k is not None:
            out.update(sets[k])
    return out


def _import_extra(rec, a, b):
    """Tests affected through import-time execution of lines a..b: code run
    while a module imports persists for every later test, and if it breaks,
    that module's import fails for every test using it."""
    il = rec.get("_import_set")
    if il is None:
        il = rec["_import_set"] = set(rec.get("import_lines") or ())
    it = rec.get("import_tests")
    sets = rec["sets"]
    out = set()
    for l in range(a, b + 1):
        if l in il:
            k = it.get(str(l)) if it is not None else None
            if k is None:
                out |= set(sets[rec["module"]])
            else:
                out.update(sets[k])
    return out


def _module_set(rec):
    return set(rec["sets"][rec["module"]])


def _container_tests(rec, idx, line, inner=None):
    """Tests for a change whose effect is at the level of the container of `line`."""
    sets = rec["sets"]
    c = idx.container(line) if inner is None else inner
    if c is None:
        return set(sets[rec["module"]])
    start, end, kind, q, node = c
    if kind == "class":
        k = rec["classes"].get(q)
        if k is None:
            raise Unknown("class not in map")
        return set(sets[k])
    # function: anyone executing it
    return _lines_tests(rec, start, end)


def _parent_container(idx, c):
    best = None
    for o in idx.containers:
        if o is not c and o[0] <= c[0] and c[1] <= o[1] and (best is None or o[1] - o[0] < best[1] - best[0]):
            best = o
    return best


def _same_def_signature_only(c, nidx):
    """True when the def with this qualname still exists with the same name and
    decorators, i.e. only arguments/defaults/annotations changed: then only calls
    (which execute the body) can observe the change."""
    if nidx is None or c is None or c[2] != "def":
        return False
    for o in nidx.containers:
        if o[2] == "def" and o[3] == c[3]:
            old, new = c[4], o[4]
            return (
                type(old) is type(new)
                and [ast.dump(d) for d in old.decorator_list] == [ast.dump(d) for d in new.decorator_list]
            )
    return False


def _assigned_names(node):
    if isinstance(node, ast.Assign) and all(isinstance(t, ast.Name) for t in node.targets):
        return [t.id for t in node.targets]
    if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
        return [node.target.id]
    return None


def _class_targets(tree):
    out = {}
    def walk(node, prefix):
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, ast.ClassDef):
                q = prefix + ch.name
                names = []
                for b in ch.body:
                    if isinstance(b, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                        for t in (b.targets if isinstance(b, ast.Assign) else [b.target]):
                            names.append(ast.dump(t))
                out[q] = names
                walk(ch, q + ".")
            elif isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(ch, prefix + ch.name + ".<locals>.")
    walk(tree, "")
    return out


def _old_line_tests(rec, idx, line, nidx=None, attr_ok=False):
    st = idx.stmt(line)
    if st is None:
        return _container_tests(rec, idx, line)
    start, end, node, header_end = st
    c = idx.container(line)
    if (
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.lineno <= line <= header_end
        and c is not None and c[4] is node
        and _same_def_signature_only(c, nidx)
    ):
        return _lines_tests(rec, header_end + 1, end) | _import_extra(rec, header_end + 1, end)
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and line <= header_end:
        # signature/decorator/base change: effect on the enclosing container + users
        parent = _parent_container(idx, c) if c is not None and c[4] is node else c
        out = _container_tests(rec, idx, line, inner=parent) if parent is not None else set(rec["sets"][rec["module"]])
        if isinstance(node, ast.ClassDef):
            k = rec["classes"].get(c[3]) if c else None
            out |= set(rec["sets"][k]) if k is not None else set()
        else:
            out |= _lines_tests(rec, start, end)
        return out
    if c is not None and c[2] == "class" and attr_ok:
        names = _assigned_names(node)
        per = (rec.get("attrs") or {}).get(c[3])
        if names and per is not None and all(n in per for n in names):
            # class data attribute: only tests that read it (via any instance or
            # subclass) can observe the new value
            sets = rec["sets"]
            out = set()
            for n in names:
                out.update(sets[per[n]])
            return out
    if c is None or c[2] == "class":
        return _container_tests(rec, idx, line, inner=c)
    # inside a function body
    if line <= header_end or not isinstance(getattr(node, "body", None), list):
        a, b = start, (header_end if line <= header_end else end)
    else:
        a, b = start, end
    return _lines_tests(rec, a, b) | _import_extra(rec, a, b)


def _scope_signature(tree):
    """Per function: bound names, generator/async-ness and global/nonlocal
    declarations. If unchanged, a statement edit cannot change how other
    statements of the function resolve names."""
    sig = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            stores, flags = set(), set()
            for m in ast.walk(n):
                if isinstance(m, ast.Name) and isinstance(m.ctx, (ast.Store, ast.Del)):
                    stores.add(m.id)
                elif isinstance(m, (ast.Yield, ast.YieldFrom, ast.Await)):
                    flags.add(type(m).__name__)
                elif isinstance(m, (ast.Global, ast.Nonlocal)):
                    flags.update(m.names)
                elif isinstance(m, ast.arg):
                    stores.add(m.arg)
            sig.append((getattr(n, "name", "<lambda>"), n.lineno, frozenset(stores), frozenset(flags)))
    return sig


_IGNORED_FIELDS = {"ctx", "lineno", "col_offset", "end_lineno", "end_col_offset", "type_comment"}


def _diff_path(a, b):
    """Ancestor chain (outermost first) down to the deepest node of `a` under
    which every difference between `a` and `b` lies; None if identical."""
    if type(a) is not type(b):
        return []
    if isinstance(a, ast.AST):
        diffs = []
        for field in a._fields:
            if field in _IGNORED_FIELDS:
                continue
            va, vb = getattr(a, field, None), getattr(b, field, None)
            if isinstance(va, list) or isinstance(vb, list):
                if not (isinstance(va, list) and isinstance(vb, list)) or len(va) != len(vb):
                    return [a]
                for x, y in zip(va, vb):
                    d = _diff_path(x, y)
                    if d is not None:
                        diffs.append(d)
            else:
                d = _diff_path(va, vb)
                if d is not None:
                    diffs.append(d)
        if not diffs:
            return None
        if len(diffs) == 1:
            return [a] + diffs[0]
        return [a]
    return None if a == b else []


_NONE_EQUIV = {(ast.Is, ast.Eq), (ast.Eq, ast.Is), (ast.IsNot, ast.NotEq), (ast.NotEq, ast.IsNot)}


def _is_none_equivalent(old_st, new_st):
    """`x is None` <-> `x == None` (and the negations): every class in sqlglot
    that defines __eq__ returns False for None, and builtins do too, so the
    rewrite cannot change any value. Only exactly that edit qualifies."""
    if old_st is None or new_st is None:
        return False
    path = _diff_path(old_st[2], new_st[2])
    if not path or not isinstance(path[-1], ast.Compare):
        return False
    old = path[-1]
    new = None
    for n in ast.walk(new_st[2]):
        if isinstance(n, ast.Compare) and ast.dump(n.left) == ast.dump(old.left) and len(n.ops) == len(old.ops) \
                and [ast.dump(c) for c in n.comparators] == [ast.dump(c) for c in old.comparators]:
            new = n
            break
    if new is None:
        return False
    operands = [old.left] + list(old.comparators)
    changed = False
    for i, (a, b) in enumerate(zip(old.ops, new.ops)):
        if type(a) is type(b):
            continue
        if (type(a), type(b)) not in _NONE_EQUIV:
            return False
        l, r = operands[i], operands[i + 1]
        if not any(isinstance(x, ast.Constant) and x.value is None for x in (l, r)):
            return False
        changed = True
    return changed


def _lambda_body_change(old_st, new_st):
    """If the only difference between two statements is inside one lambda's
    body (not its arguments), return that lambda node of the old tree."""
    if old_st is None or new_st is None:
        return None
    path = _diff_path(old_st[2], new_st[2])
    if not path:
        return None
    for i, node in enumerate(path):
        if isinstance(node, ast.Lambda) and i + 1 < len(path):
            below = path[i + 1]
            if below is node.body or any(below is n for n in ast.walk(node.body)):
                return node
    return None


_PURE = (ast.Constant, ast.Tuple, ast.List, ast.Set, ast.Dict, ast.Name, ast.Attribute,
         ast.Load, ast.UnaryOp, ast.USub, ast.UAdd, ast.Not, ast.Invert, ast.BinOp,
         ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow,
         ast.BitOr, ast.BitAnd, ast.BitXor, ast.LShift, ast.RShift,
         # factory calls (rename_func(...), _show_parser(...)) run once at class
         # creation and their result is only stored in the dict
         ast.Call, ast.keyword)


def _pure_value(node):
    """An expression whose evaluation at class creation only builds the stored
    value (names, literals, operators, factory calls on plain arguments)."""
    stack = [node]
    while stack:
        n = stack.pop()
        if isinstance(n, ast.Lambda):
            a = n.args
            stack.extend(d for d in a.defaults + a.kw_defaults if d is not None)
            continue
        if not isinstance(n, _PURE):
            return False
        stack.extend(ast.iter_child_nodes(n))
    return True


def _key_candidates(k):
    if isinstance(k, ast.Constant):
        return {repr(k.value)}
    if isinstance(k, ast.Tuple):
        try:
            return {repr(ast.literal_eval(k))}
        except ValueError:
            return set()
    parts = []
    n = k
    while isinstance(n, ast.Attribute):
        parts.append(n.attr); n = n.value
    if not isinstance(n, ast.Name):
        return set()
    parts.append(n.id)
    parts.reverse()
    # runtime tokens: a class's __name__ or an enum member's name
    return {parts[-1]}


def _dict_value_tests(rec, oidx, nidx, old_line, new_line, attr_ok):
    """Class-attribute dict literal whose keys are unchanged and whose changed
    values are plain expressions: only tests that read those keys' values (or
    the whole dict) can observe the edit. None when the rule does not apply."""
    if not attr_ok:
        return None
    so, sn = oidx.stmt(old_line), nidx.stmt(new_line)
    co, cn = oidx.container(old_line), nidx.container(new_line)
    if not so or not sn or co is None or cn is None or co[2] != "class" or co[3] != cn[3]:
        return None
    o, n = so[2], sn[2]
    names = _assigned_names(o)
    if type(o) is not type(n) or not names or len(names) != 1 or names != _assigned_names(n):
        return None
    if isinstance(o, ast.AnnAssign) and ast.dump(o.annotation) != ast.dump(n.annotation):
        return None
    vo, vn = o.value, n.value
    if not isinstance(vo, ast.Dict) or not isinstance(vn, ast.Dict) or len(vo.keys) != len(vn.keys):
        return None
    dump = lambda k: "**" if k is None else ast.dump(k)
    if [dump(k) for k in vo.keys] != [dump(k) for k in vn.keys]:
        return None
    dk = ((rec.get("dictkeys") or {}).get(co[3]) or {}).get(names[0])
    if not dk:
        return None
    rt = dk["keys"]
    if all(k is not None for k in vo.keys):
        # plain literal: runtime keys line up one-to-one with the source keys
        if len(rt) != len(vo.keys) or any(t not in _key_candidates(k) for t, k in zip(rt, vo.keys)):
            return None
        toks = [{t} for t in rt]
    else:
        # with ** spreads the runtime order differs: match each explicit key by name
        have = set(rt)
        toks = [None if k is None else (_key_candidates(k) & have) for k in vo.keys]
        if any(k is not None and not t for k, t in zip(vo.keys, toks)):
            return None
    sets = rec["sets"]
    out = set(sets[dk["all"]])
    for k, ts, a, b in zip(vo.keys, toks, vo.values, vn.values):
        if ast.dump(a) != ast.dump(b):
            if k is None or not (_pure_value(a) and _pure_value(b)):
                return None
            for t in ts:
                r = dk["by"].get(t)
                if r is None:
                    return None
                out |= set(sets[r])
    return out


_CMP_TWINS = {(ast.Lt, ast.LtE), (ast.LtE, ast.Lt), (ast.Gt, ast.GtE), (ast.GtE, ast.Gt),
              (ast.Is, ast.Eq), (ast.Eq, ast.Is), (ast.IsNot, ast.NotEq), (ast.NotEq, ast.IsNot)}


def _walk_parents(tree):
    """ast.walk order (breadth first) with each node's parent index, so that
    shared operator singletons (ast.Lt, ast.And, ...) can be told apart."""
    nodes, parents, i = [tree], [None], 0
    while i < len(nodes):
        for c in ast.iter_child_nodes(nodes[i]):
            nodes.append(c)
            parents.append(i)
        i += 1
    return nodes, parents


def _one_op_swap(so, sn, kind):
    """The single position where old and new statements differ in node type,
    if it is an operator of `kind`: (old nodes, parents, index), else None."""
    lo, po = _walk_parents(so)
    ln, _ = _walk_parents(sn)
    if len(lo) != len(ln):
        return None
    diff = [i for i, (a, b) in enumerate(zip(lo, ln)) if type(a) is not type(b)]
    if len(diff) != 1 or not isinstance(lo[diff[0]], kind):
        return None
    return lo, ln, po, diff[0]


def _same_but_op(so, sn, nnode, field, old_op):
    saved = getattr(nnode, field)
    setattr(nnode, field, old_op)
    same = ast.dump(so) == ast.dump(sn)
    setattr(nnode, field, saved)
    return same


def _cmp_evidence(rec, key, node):
    """Tests that recorded the two forms disagreeing at `key`, plus tests
    whose execution of the node's lines varied between collection runs."""
    cmp, unstable = rec["cmp"], rec.get("unstable")
    if not isinstance(unstable, dict):
        return None
    if key in cmp and cmp[key] is None:
        return None  # could differ while modules were importing
    out = set(rec["sets"][cmp[key]]) if key in cmp else set()
    for l in range(node.lineno, node.end_lineno + 1):
        k = unstable.get(str(l))
        if k is not None:
            out.update(rec["sets"][k])
    return out


def _cmp_swap_tests(rec, oidx, nidx, old_line, new_line):
    """One comparison operator swapped with its twin (< <=, > >=, is ==,
    is not !=), everything else identical: only tests in which that comparison
    saw equal operands (or `is` and `==` disagreeing) can observe it. None when
    the rule does not apply."""
    if rec.get("cmp") is None:
        return None
    so, sn = oidx.stmt(old_line), nidx.stmt(new_line)
    if not so or not sn:
        return None
    sw = _one_op_swap(so[2], sn[2], ast.cmpop)
    if sw is None:
        return None
    lo, ln, po, i = sw
    if (type(lo[i]), type(ln[i])) not in _CMP_TWINS:
        return None
    node, nnode = lo[po[i]], ln[po[i]]
    if not isinstance(node, ast.Compare) or len(node.ops) != 1:
        return None
    r = node.comparators[0]
    if isinstance(r, ast.Constant) and r.value is None:
        return None  # not recorded (`is None` has its own rule)
    if not _same_but_op(so[2], sn[2], nnode, "ops", [type(lo[i])()]):
        return None
    return _cmp_evidence(rec, "%d:%d" % (node.lineno, node.col_offset), node)


_DEFAULT_TYPES = (bool, int, str, float, bytes, type(None))


def _default_value_tests(rec, oidx, nidx, old_line, new_line):
    """Only one constant default of a function changed: the function body is
    unchanged, so only calls in which the parameter held the old default value
    (omitted, or passed equal) can behave differently."""
    if rec.get("cmp") is None:
        return None
    so, sn = oidx.stmt(old_line), nidx.stmt(new_line)
    if not so or not sn:
        return None
    fo, fn = so[2], sn[2]
    if not isinstance(fo, (ast.FunctionDef, ast.AsyncFunctionDef)) or type(fo) is not type(fn):
        return None
    ao, an = fo.args, fn.args
    if len(ao.defaults) != len(an.defaults) or len(ao.kw_defaults) != len(an.kw_defaults):
        return None
    pos = ao.posonlyargs + ao.args
    slots = [(p, "defaults", i) for i, p in enumerate(pos[len(pos) - len(ao.defaults):])] + \
        [(p, "kw_defaults", i) for i, p in enumerate(ao.kwonlyargs) if ao.kw_defaults[i] is not None]
    changed = []
    for p, field, i in slots:
        do, dn = getattr(ao, field)[i], getattr(an, field)[i]
        if dn is None or ast.dump(do) != ast.dump(dn):
            changed.append((p, field, i, do, dn))
    if len(changed) != 1:
        return None
    p, field, i, do, dn = changed[0]
    if not isinstance(do, ast.Constant) or type(do.value) not in _DEFAULT_TYPES:
        return None
    if not isinstance(dn, ast.Constant):
        return None
    lst = getattr(an, field)
    lst[i] = do
    same = ast.dump(fo) == ast.dump(fn)
    lst[i] = dn
    if not same:
        return None
    if len(fo.body) <= (1 if (isinstance(fo.body[0], ast.Expr) and isinstance(fo.body[0].value, ast.Constant)
                              and isinstance(fo.body[0].value.value, str)) else 0):
        return None  # not instrumented (docstring-only body)
    key = "D:%d:%s" % (fo.lineno, p.arg)
    if key not in rec["cmp"]:
        return None  # map built without default-use evidence for this function
    return _cmp_evidence(rec, key, fo)


def _bool_context(node, parent):
    """Mirror of the collector: a two-value BoolOp whose value is only used
    for its truth (if/while/ternary/assert test, comprehension filter, `not`)."""
    if not isinstance(node, ast.BoolOp) or len(node.values) != 2:
        return False
    if isinstance(parent, (ast.If, ast.While, ast.IfExp, ast.Assert)):
        return parent.test is node
    if isinstance(parent, ast.comprehension):
        return any(t is node for t in parent.ifs)
    if isinstance(parent, ast.UnaryOp):
        return isinstance(parent.op, ast.Not) and parent.operand is node
    return False


def _boolop_swap_tests(rec, oidx, nidx, old_line, new_line):
    """`a and b` <-> `a or b` used only for its truth, everything else
    identical: only tests in which the two forms would have differed (or the
    swapped form would have evaluated b) can observe it."""
    if rec.get("cmp") is None:
        return None
    so, sn = oidx.stmt(old_line), nidx.stmt(new_line)
    if not so or not sn:
        return None
    sw = _one_op_swap(so[2], sn[2], ast.boolop)
    if sw is None:
        return None
    lo, ln, po, i = sw
    node, nnode = lo[po[i]], ln[po[i]]
    if po[po[i]] is None or not _bool_context(node, lo[po[po[i]]]):
        return None
    if not _same_but_op(so[2], sn[2], nnode, "op", type(lo[i])()):
        return None
    return _cmp_evidence(rec, "B:%d:%d" % (node.lineno, node.col_offset), node)


def select_file(rec, hunks):
    source = rec["source"]
    new_source, deleted, added = apply(source, hunks)
    old_tree = ast.parse(source)
    try:
        new_tree = ast.parse(new_source)
    except (SyntaxError, ValueError):
        raise Unknown("syntax error")
    if ast.dump(old_tree) == ast.dump(new_tree):
        return set(rec["sets"][rec["readers"]])
    old_lines = source.split("\n")
    new_lines = new_source.split("\n")
    old_spans, new_spans = _string_spans(old_tree), _string_spans(new_tree)
    oidx = _Index(old_tree)
    nidx = _Index(new_tree)
    out = set(rec["sets"][rec["readers"]])
    # class attribute rule only when no class gains, loses or renames an attribute
    attr_ok = _class_targets(old_tree) == _class_targets(new_tree)
    scope_same = _scope_signature(old_tree) == _scope_signature(new_tree)
    paired_old = {p for _, _, _, p in added if p is not None} if scope_same else set()
    for line in deleted:
        if line in paired_old or _is_noop_line(old_lines[line - 1], line, old_spans):
            continue
        out |= _old_line_tests(rec, oidx, line, nidx, attr_ok)
    for new_line, before_old, has_del, paired in added:
        if paired is not None and scope_same:
            if _is_noop_line(new_lines[new_line - 1], new_line, new_spans) and _is_noop_line(
                old_lines[paired - 1], paired, old_spans
            ):
                continue
            # one-for-one line replacement: the old statement rule covers it,
            # narrowed to a lambda's executions when only its body changed
            if _is_none_equivalent(oidx.stmt(paired), nidx.stmt(new_line)):
                continue
            lam = _lambda_body_change(oidx.stmt(paired), nidx.stmt(new_line))
            if lam is not None:
                # a one-line lambda's line also runs when it is defined, so only
                # continuation lines can show import-time calls
                out |= _lines_tests(rec, lam.lineno, lam.end_lineno)
                if lam.end_lineno > lam.lineno:
                    out |= _import_extra(rec, lam.lineno + 1, lam.end_lineno)
            else:
                dv = _cmp_swap_tests(rec, oidx, nidx, paired, new_line)
                if dv is None:
                    dv = _boolop_swap_tests(rec, oidx, nidx, paired, new_line)
                if dv is None:
                    dv = _default_value_tests(rec, oidx, nidx, paired, new_line)
                if dv is None:
                    dv = _dict_value_tests(rec, oidx, nidx, paired, new_line, attr_ok)
                out |= dv if dv is not None else _old_line_tests(rec, oidx, paired, nidx, attr_ok)
            continue
        if _is_noop_line(new_lines[new_line - 1], new_line, new_spans):
            continue
        if has_del:
            # replacement: map to the old statement at the same spot as well
            ref = min(max(before_old - 1, 1), len(old_lines))
            out |= _old_line_tests(rec, oidx, ref, nidx)
        # insertion: effect on the container in the new tree (same qualname in old)
        c = nidx.container(new_line)
        st = nidx.stmt(new_line)
        if c is not None and st is not None and st[2] is c[4] and new_line <= st[3]:
            c = _parent_container(nidx, c)  # header line of a def/class
        if c is None:
            out |= set(rec["sets"][rec["module"]])
            continue
        kind, q = c[2], c[3]
        if kind == "class":
            k = rec["classes"].get(q)
            if k is None:
                raise Unknown("new class")
            out |= set(rec["sets"][k])
        else:
            match = [o for o in oidx.containers if o[3] == q and o[2] == "def"]
            if not match:
                raise Unknown("new function")
            fn = match[0][4]
            body_start = fn.body[0].lineno
            out |= _lines_tests(rec, match[0][0], match[0][1])
            out |= _import_extra(rec, body_start, match[0][1])
    return out


def select(patch_text, cmap):
    files = parse_patch(patch_text)
    out = set()
    for old, new, hunks in files:
        if old is None and new is None:
            raise Unknown("empty paths")
        if old != new:
            if old is None:  # new file: only directory listers can see it
                parts = new.split("/")
                for k in range(len(parts) - 1, 0, -1):
                    out.update(cmap["dirs"].get("/".join(parts[:k]), ()))
                if new.endswith(".py") and new.startswith(("sqlglot/", "tests/")):
                    # a new module nobody imports; a new test file is outside the catalog
                    continue
                continue
            raise Unknown("rename or deletion")
        path = old
        rec = cmap["files"].get(path)
        if rec is not None:
            out |= select_file(rec, hunks)
        elif path in cmap["data"]:
            out.update(cmap["data"][path])
        elif path.endswith(".py") and path.startswith(("sqlglot/", "tests/")):
            raise Unknown("unmapped python file")
        else:
            # never opened by any test at the pinned commit
            parts = path.split("/")
            for k in range(len(parts) - 1, 0, -1):
                out.update(cmap["dirs"].get("/".join(parts[:k]), ()))
    return out
