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
        for k, v in body:
            if k != "+":
                if cur >= len(old) or old[cur] != v:
                    raise Unknown("context mismatch")
                if k == "-":
                    deleted.append(cur + 1)
                cur += 1
            if k != "-":
                if k == "+":
                    added.append((len(out) + 1, cur + 1, has_del))
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


def _old_line_tests(rec, idx, line):
    st = idx.stmt(line)
    if st is None:
        return _container_tests(rec, idx, line)
    start, end, node, header_end = st
    c = idx.container(line)
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
    if c is None or c[2] == "class":
        return _container_tests(rec, idx, line, inner=c)
    # inside a function body
    if line <= header_end or not isinstance(getattr(node, "body", None), list):
        return _lines_tests(rec, start, header_end if line <= header_end else end)
    return _lines_tests(rec, start, end)


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
    for line in deleted:
        if _is_noop_line(old_lines[line - 1], line, old_spans):
            continue
        out |= _old_line_tests(rec, oidx, line)
    for new_line, before_old, has_del in added:
        if _is_noop_line(new_lines[new_line - 1], new_line, new_spans):
            continue
        if has_del:
            # replacement: map to the old statement at the same spot as well
            ref = min(max(before_old - 1, 1), len(old_lines))
            out |= _old_line_tests(rec, oidx, ref)
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
            out |= _lines_tests(rec, match[0][0], match[0][1])
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
