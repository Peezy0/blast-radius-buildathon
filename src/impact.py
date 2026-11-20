"""Conservative, optional selection from dependency evidence for the pinned source.

``choose_tests(patch_text, test_ids, coverage=None)`` returns a set of test IDs.
Without sufficient evidence it returns *all* supplied IDs. It never executes a
patch, interprets a sample answer, or classifies comments as harmless.

The JSON-serializable coverage format is intentionally explicit::

    {
      "version": 1,
      "baseline_commit": BASELINE_COMMIT,
      "files": {
        "sqlglot/example.py": {
          "source": "<exact pinned source text>",
          "complete": true,
          "functions": {"Example.parse": [1, 2]},
          "file_dependencies": [3],
          "lines": {"42": [4]}
        }
      }
    }

Function names are lexical dotted names, including enclosing classes/functions.
Function mappings must include every known transitive dependent, not merely
tests that directly call a function. ``file_dependencies`` is required and
includes file readers, relevant collection/fixture dependencies, and other
dependencies that cannot be attributed to one function. ``lines`` is optional
and can only *add* tests; line-only evidence never justifies excluding tests.
An empty/missing function mapping is unknown evidence and causes full fallback.
The producer must assert ``complete`` only after collecting all catalog tests.

Only body changes to already-known Python functions are narrowed. Imports,
module/class scope changes, changed signatures, new/deleted files or symbols,
invalid patches, missing evidence, and syntax errors fall back to all tests.
Baseline observations cannot prove arbitrary changed-program behavior; this is
a conservative dependency heuristic, not a guarantee of perfect failure recall.
"""

from __future__ import annotations

import ast
import copy
from dataclasses import dataclass
from pathlib import PurePosixPath
import re
from typing import Any, Hashable, Iterable, Mapping


BASELINE_COMMIT = "eb11aec4be8cb2122afe713a4816662c3f0fb2fa"
_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:.*)$")
_NO_NEWLINE = "\\ No newline at end of file"


class _Unknown(ValueError):
    """The patch or evidence cannot safely support a reduced selection."""


@dataclass(frozen=True)
class _Hunk:
    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class _FilePatch:
    path: str
    hunks: tuple[_Hunk, ...]


def _path(header: str, prefix: str) -> str:
    value = header[len(prefix):].rstrip("\n").split("\t", 1)[0]
    if value.startswith(("a/", "b/")):
        value = value[2:]
    path = PurePosixPath(value)
    if (
        not value
        or value == "/dev/null"
        or value.startswith(("/", '"'))
        or "\\" in value
        or any(ord(char) < 32 for char in value)
        or ".." in path.parts
        or path.as_posix() != value
    ):
        raise _Unknown("unsupported path")
    return value


def _parse_patch(text: str) -> tuple[_FilePatch, ...]:
    if not isinstance(text, str) or not text or "\x00" in text:
        raise _Unknown("missing or binary diff")
    lines = text.splitlines(keepends=True)
    files: list[_FilePatch] = []
    seen: set[str] = set()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith(("diff --git ", "index ")):
            i += 1
            continue
        if not line.startswith("--- ") or i + 1 >= len(lines):
            raise _Unknown("unsupported diff metadata")
        old_path = _path(line, "--- ")
        if not lines[i + 1].startswith("+++ "):
            raise _Unknown("missing new-file header")
        new_path = _path(lines[i + 1], "+++ ")
        if old_path != new_path or old_path in seen or not old_path.endswith(".py"):
            raise _Unknown("unmapped, duplicate, renamed, or non-Python file")
        seen.add(old_path)
        i += 2
        hunks: list[_Hunk] = []
        while i < len(lines) and lines[i].startswith("@@ "):
            match = _HUNK.fullmatch(lines[i].rstrip("\n"))
            if match is None:
                raise _Unknown("invalid hunk header")
            old_start, old_count, new_start, new_count = (
                int(match[1]), int(match[2] or 1), int(match[3]), int(match[4] or 1)
            )
            if (old_count and old_start == 0) or (new_count and new_start == 0):
                raise _Unknown("invalid zero line number")
            i += 1
            old_read = new_read = 0
            body: list[tuple[str, str]] = []
            while old_read < old_count or new_read < new_count:
                if i >= len(lines) or lines[i][0] not in " +-":
                    raise _Unknown("truncated hunk")
                kind, value = lines[i][0], lines[i][1:]
                i += 1
                if i < len(lines) and lines[i].rstrip("\n") == _NO_NEWLINE:
                    value = value.removesuffix("\n")
                    i += 1
                old_read += kind != "+"
                new_read += kind != "-"
                if old_read > old_count or new_read > new_count:
                    raise _Unknown("hunk counts disagree")
                body.append((kind, value))
            if not any(kind != " " for kind, _ in body):
                raise _Unknown("hunk contains no edit")
            hunks.append(_Hunk(old_start, old_count, new_start, new_count, tuple(body)))
        if not hunks:
            raise _Unknown("file has no hunks")
        files.append(_FilePatch(old_path, tuple(hunks)))
    if not files:
        raise _Unknown("diff contains no files")
    return tuple(files)


def _apply(source: str, patch: _FilePatch) -> tuple[str, set[int], set[int]]:
    original = source.splitlines(keepends=True)
    output: list[str] = []
    cursor = 0
    deleted: set[int] = set()
    added: set[int] = set()
    for hunk in patch.hunks:
        start = hunk.old_start - 1 if hunk.old_count else hunk.old_start
        if start < cursor or start > len(original):
            raise _Unknown("overlapping or out-of-bounds hunks")
        output.extend(original[cursor:start])
        cursor = start
        expected_new = hunk.new_start - 1 if hunk.new_count else hunk.new_start
        if expected_new != len(output):
            raise _Unknown("new hunk position disagrees with patch")
        for kind, value in hunk.lines:
            if kind != "+":
                if cursor >= len(original) or original[cursor] != value:
                    raise _Unknown("patch does not match pinned source")
                if kind == "-":
                    deleted.add(cursor + 1)
                cursor += 1
            if kind != "-":
                if kind == "+":
                    added.add(len(output) + 1)
                output.append(value)
    output.extend(original[cursor:])
    return "".join(output), deleted, added


class _BodiesRemoved(ast.NodeTransformer):
    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.FunctionDef:
        node.body = [ast.Pass()]
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.AsyncFunctionDef:
        node.body = [ast.Pass()]
        return node


def _dump_without_bodies(tree: ast.AST) -> str:
    return ast.dump(_BodiesRemoved().visit(copy.deepcopy(tree)), include_attributes=False)


def _functions(tree: ast.AST) -> dict[str, ast.FunctionDef | ast.AsyncFunctionDef]:
    found: dict[str, ast.FunctionDef | ast.AsyncFunctionDef] = {}

    def walk(node: ast.AST, parents: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            parents = (*parents, node.name)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = ".".join(parents)
                if name in found:
                    raise _Unknown("ambiguous repeated function definition")
                found[name] = node
        for child in ast.iter_child_nodes(node):
            walk(child, parents)

    walk(tree, ())
    return found


def _owning_function(line: int, functions: Mapping[str, Any]) -> str:
    candidates = [
        (node.end_lineno - node.lineno, name)
        for name, node in functions.items()
        if node.body[0].lineno <= line <= node.end_lineno
    ]
    if not candidates:
        raise _Unknown("edit is outside an existing function body")
    return min(candidates)[1]


def _ids(value: Any, universe: set[Hashable], *, allow_empty: bool) -> set[Hashable]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise _Unknown("dependency IDs are missing")
    result = set(value)
    if not result <= universe or (not allow_empty and not result):
        raise _Unknown("dependency IDs are unknown or unobserved")
    return result


def choose_tests(
    patch_text: str,
    test_ids: Iterable[Hashable],
    coverage: Mapping[str, Any] | None = None,
) -> set[Hashable]:
    """Choose affected IDs, returning all catalog IDs for any unknown evidence.

    The return value is always a set and does not include a reason or mutate its
    arguments. IDs may be integers or strings but must match the catalog exactly.
    """
    universe = set(test_ids)
    if not universe:
        return universe
    try:
        if (
            not isinstance(coverage, Mapping)
            or coverage.get("version") != 1
            or coverage.get("baseline_commit") != BASELINE_COMMIT
            or not isinstance(coverage.get("files"), Mapping)
        ):
            raise _Unknown("missing or incompatible baseline evidence")
        selected: set[Hashable] = set()
        for patch in _parse_patch(patch_text):
            record = coverage["files"].get(patch.path)
            if not isinstance(record, Mapping) or record.get("complete") is not True:
                raise _Unknown("unmapped or incomplete file evidence")
            source = record.get("source")
            if not isinstance(source, str):
                raise _Unknown("missing pinned source")
            changed, deleted, added = _apply(source, patch)
            old_tree = ast.parse(source, filename=patch.path)
            new_tree = ast.parse(changed, filename=patch.path)
            compile(new_tree, patch.path, "exec")
            if _dump_without_bodies(old_tree) != _dump_without_bodies(new_tree):
                raise _Unknown("module, class, signature, or decorator changed")
            old_imports = [ast.dump(n) for n in ast.walk(old_tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
            new_imports = [ast.dump(n) for n in ast.walk(new_tree) if isinstance(n, (ast.Import, ast.ImportFrom))]
            if old_imports != new_imports:
                raise _Unknown("import changed")
            old_functions, new_functions = _functions(old_tree), _functions(new_tree)
            if old_functions.keys() != new_functions.keys():
                raise _Unknown("a function was added or removed")
            for name in old_functions:
                if _dump_without_bodies(old_functions[name]) != _dump_without_bodies(new_functions[name]):
                    raise _Unknown("a nested function signature changed")
            affected = {_owning_function(line, old_functions) for line in deleted}
            affected.update(_owning_function(line, new_functions) for line in added)
            if not affected or not isinstance(record.get("functions"), Mapping):
                raise _Unknown("no mapped affected function")
            selected.update(_ids(record.get("file_dependencies"), universe, allow_empty=True))
            for name in affected:
                selected.update(_ids(record["functions"].get(name), universe, allow_empty=False))
            line_map = record.get("lines", {})
            if not isinstance(line_map, Mapping):
                raise _Unknown("invalid line evidence")
            for line in deleted:
                if str(line) in line_map:
                    selected.update(_ids(line_map[str(line)], universe, allow_empty=True))
        return selected or universe
    except (ValueError, TypeError, KeyError, SyntaxError, RecursionError, AttributeError):
        return universe
