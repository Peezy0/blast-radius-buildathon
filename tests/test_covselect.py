"""Regression tests for src/covselect.py on a small synthetic map."""

import difflib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.covselect import Unknown, select

SRC = '''import os

LIMIT = 3


class Box:
    SIZE = 1
    HOOKS = {
        "a": lambda x: x + 1,
        "b": lambda x: x * 2,
    }

    def grow(self, n=1):
        total = 0
        if n > LIMIT:
            total = n
        return total + self.SIZE


def helper(v):
    return v
'''


def make_map():
    # tests: 0 runs grow with big n, 1 runs grow with small n, 2 calls hook a,
    # 3 calls hook b, 4 reads fixture, 5 instantiates Box only
    sets = [[0], [0, 1], [2], [3], [0, 1, 5], [0, 1, 2, 3, 5], [], [0, 1]]
    lines = {
        "14": 1, "15": 1, "16": 0, "17": 1,  # grow body
        "9": 2, "10": 3,  # lambda bodies
        "21": 6,
    }
    return {
        "tests": ["t0", "t1", "t2", "t3", "t4", "t5"],
        "files": {
            "pkg/box.py": {
                "source": SRC,
                "lines": lines,
                "module": 5,
                "classes": {"Box": 4},
                "readers": 6,
                "unstable": {},
                "attrs": {"Box": {"SIZE": 7}},
                "sets": sets,
            }
        },
        "data": {"tests/fixtures/f.sql": [4]},
        "dirs": {},
    }


def patch(old, new, path="pkg/box.py"):
    return "".join(difflib.unified_diff(
        old.splitlines(True), new.splitlines(True), "a/" + path, "b/" + path, n=3))


class CovSelectTests(unittest.TestCase):
    def setUp(self):
        self.map = make_map()

    def sel(self, new):
        return select(patch(SRC, new), self.map)

    def test_statement_only_executed_by_some_tests(self):
        self.assertEqual(self.sel(SRC.replace("total = n", "total = n + 1")), {0})

    def test_condition_line(self):
        self.assertEqual(self.sel(SRC.replace("if n > LIMIT", "if n >= LIMIT")), {0, 1})

    def test_never_executed_statement(self):
        self.assertEqual(self.sel(SRC.replace("return v", "return -v")), set())

    def test_comment_only(self):
        self.assertEqual(self.sel(SRC.replace("        total = 0\n", "        # note\n        total = 0\n")), set())

    def test_inserted_statement_uses_function(self):
        new = SRC.replace("        total = 0\n", "        total = 0\n        n = abs(n)\n")
        self.assertEqual(self.sel(new), {0, 1})

    def test_lambda_body_only(self):
        self.assertEqual(self.sel(SRC.replace("x * 2", "x * 3")), {3})

    def test_lambda_key_change_is_class_level(self):
        self.assertEqual(self.sel(SRC.replace('"b": lambda', '"c": lambda')), {0, 1, 5})

    def test_class_attribute_readers(self):
        self.assertEqual(self.sel(SRC.replace("SIZE = 1", "SIZE = 2")), {0, 1})

    def test_module_level_constant(self):
        self.assertEqual(self.sel(SRC.replace("LIMIT = 3", "LIMIT = 4")), {0, 1, 2, 3, 5})

    def test_default_argument(self):
        self.assertEqual(self.sel(SRC.replace("n=1", "n=2")), {0, 1})

    def test_new_local_name_widens_to_function(self):
        new = SRC.replace("total = n\n", "totl = n\n")
        self.assertEqual(self.sel(new), {0, 1})

    def test_import_time_function_widens_to_module(self):
        self.map["files"]["pkg/box.py"]["import_lines"] = [21]
        self.assertEqual(self.sel(SRC.replace("return v", "return -v")), {0, 1, 2, 3, 5})

    def test_is_none_to_eq_none_is_equivalent(self):
        src2 = SRC.replace("if n > LIMIT:", "if n is not None:")
        self.map["files"]["pkg/box.py"]["source"] = src2
        p = patch(src2, src2.replace("if n is not None:", "if n != None:"))
        self.assertEqual(select(p, self.map), set())
        p = patch(src2, src2.replace("if n is not None:", "if n is None:"))
        self.assertEqual(select(p, self.map), {0, 1})

    def test_dict_value_change_uses_key_readers(self):
        self.map["files"]["pkg/box.py"]["dictkeys"] = {
            "Box": {"HOOKS": {"keys": ["'a'", "'b'"], "all": 6, "by": {"'a'": 2, "'b'": 3}}}}
        src2 = SRC.replace("lambda x: x + 1", "(1, 2)").replace("lambda x: x * 2", "(3, 4)")
        self.map["files"]["pkg/box.py"]["source"] = src2
        self.assertEqual(select(patch(src2, src2.replace("(3, 4)", "(3, 5)")), self.map), {3})
        self.assertEqual(select(patch(src2, src2.replace("(1, 2)", "(1, -2)")), self.map), {2})

    def test_dict_value_change_needs_matching_keys(self):
        self.map["files"]["pkg/box.py"]["dictkeys"] = {
            "Box": {"HOOKS": {"keys": ["'a'", "'c'"], "all": 6, "by": {"'a'": 2, "'c'": 3}}}}
        self.assertEqual(self.sel(SRC.replace('"b": lambda x: x * 2', '"b": lambda y: y * 2')), {0, 1, 5})

    def test_dict_value_with_scope_change_uses_class_rule(self):
        self.map["files"]["pkg/box.py"]["dictkeys"] = {
            "Box": {"HOOKS": {"keys": ["'a'", "'b'"], "all": 6, "by": {"'a'": 2, "'b'": 3}}}}
        self.assertEqual(self.sel(SRC.replace('"b": lambda x: x * 2', '"b": helper(2)')), {0, 1, 5})

    def test_boundary_swap_uses_equal_operand_tests(self):
        self.map["files"]["pkg/box.py"]["cmp"] = {"15:11": 2}
        self.assertEqual(self.sel(SRC.replace("if n > LIMIT", "if n >= LIMIT")), {2})

    def test_boundary_swap_never_equal_selects_nothing(self):
        self.map["files"]["pkg/box.py"]["cmp"] = {}
        self.assertEqual(self.sel(SRC.replace("if n > LIMIT", "if n >= LIMIT")), set())

    def test_boundary_swap_without_evidence_uses_lines(self):
        self.assertEqual(self.sel(SRC.replace("if n > LIMIT", "if n >= LIMIT")), {0, 1})
        self.map["files"]["pkg/box.py"]["cmp"] = {"15:11": None}
        self.assertEqual(self.sel(SRC.replace("if n > LIMIT", "if n >= LIMIT")), {0, 1})

    def _boolsrc(self, old):
        src = SRC.replace("if n > LIMIT:", old)
        self.map["files"]["pkg/box.py"]["source"] = src
        return src

    def test_and_or_swap_in_condition_uses_evidence(self):
        src = self._boolsrc("if n > LIMIT and n:")
        self.map["files"]["pkg/box.py"]["cmp"] = {"B:15:11": 2}
        self.assertEqual(select(patch(src, src.replace(" and n:", " or n:")), self.map), {2})
        self.map["files"]["pkg/box.py"]["cmp"] = {}
        self.assertEqual(select(patch(src, src.replace(" and n:", " or n:")), self.map), set())

    def test_and_or_swap_without_evidence_uses_lines(self):
        src = self._boolsrc("if n > LIMIT and n:")
        new = src.replace(" and n:", " or n:")
        self.assertEqual(select(patch(src, new), self.map), {0, 1})
        self.map["files"]["pkg/box.py"]["cmp"] = {"B:15:11": None}
        self.assertEqual(select(patch(src, new), self.map), {0, 1})

    def test_and_or_swap_outside_condition_uses_lines(self):
        src = self._boolsrc("if (n > LIMIT and n) == 1:")
        self.map["files"]["pkg/box.py"]["cmp"] = {}
        new = src.replace(" and n)", " or n)")
        self.assertEqual(select(patch(src, new), self.map), {0, 1})

    def test_and_or_swap_of_second_boolop_uses_its_own_key(self):
        src = self._boolsrc("if not (n and LIMIT) and (n > 0 and n):")
        # outer BoolOp at col 11, `n > 0 and n` at col 34: only the latter swapped
        self.map["files"]["pkg/box.py"]["cmp"] = {"B:15:11": 3}
        new = src.replace("(n > 0 and n)", "(n > 0 or n)")
        self.assertEqual(select(patch(src, new), self.map), {0, 1})  # not in a bool context
        new = src.replace("(n and LIMIT)", "(n or LIMIT)")
        self.map["files"]["pkg/box.py"]["cmp"] = {"B:15:11": 3, "B:15:16": 2}
        self.assertEqual(select(patch(src, new), self.map), {2})

    def test_second_comparison_swap_uses_its_own_key(self):
        src = self._boolsrc("if n > 0 and n > LIMIT:")
        self.map["files"]["pkg/box.py"]["cmp"] = {"15:11": 3, "15:21": 2}
        new = src.replace("n > LIMIT", "n >= LIMIT")
        self.assertEqual(select(patch(src, new), self.map), {2})

    def test_swap_adds_tests_whose_execution_of_the_node_varied(self):
        rec = self.map["files"]["pkg/box.py"]
        rec["sets"].append([1])
        rec["sets"].append([3])
        rec["unstable"] = {"15": len(rec["sets"]) - 2, "16": len(rec["sets"]) - 1}
        rec["cmp"] = {"15:11": 2}
        # test 1's execution of line 15 varied between runs; test 3 varied elsewhere
        self.assertEqual(self.sel(SRC.replace("if n > LIMIT", "if n >= LIMIT")), {1, 2})

    def test_swap_without_unstable_record_uses_lines(self):
        del self.map["files"]["pkg/box.py"]["unstable"]
        self.map["files"]["pkg/box.py"]["cmp"] = {"15:11": 2}
        self.assertEqual(self.sel(SRC.replace("if n > LIMIT", "if n >= LIMIT")), {0, 1})

    def test_docstring_text_change_selects_only_source_readers(self):
        src = SRC.replace("def helper(v):\n", 'def helper(v):\n    """Return v."""\n')
        self.map["files"]["pkg/box.py"]["source"] = src
        new = src.replace("Return v.", "Return the value v.")
        self.assertEqual(select(patch(src, new), self.map), set())

    def test_docstring_emptied_is_not_text_only(self):
        src = SRC.replace("def helper(v):\n", 'def helper(v):\n    """Return v."""\n')
        self.map["files"]["pkg/box.py"]["source"] = src
        new = src.replace('"""Return v."""', '""""""')
        # falls through to the line rules (helper is run by no test here)
        self.map["files"]["pkg/box.py"]["readers"] = 7
        self.assertEqual(select(patch(src, new), self.map), {0, 1})
        import ast
        from src.covselect import _docstrings_blanked
        self.assertIsNone(_docstrings_blanked(ast.parse(new)))
        self.assertIsNotNone(_docstrings_blanked(ast.parse(src)))

    def test_direction_flip_is_not_a_twin(self):
        self.map["files"]["pkg/box.py"]["cmp"] = {}
        self.assertEqual(self.sel(SRC.replace("if n > LIMIT", "if n < LIMIT")), {0, 1})

    def test_fixture(self):
        p = patch("SELECT 1;\n", "SELECT 2;\n", "tests/fixtures/f.sql")
        self.assertEqual(select(p, self.map), {4})

    def test_syntax_error_is_unknown(self):
        with self.assertRaises(Unknown):
            self.sel(SRC.replace("return total + self.SIZE", "return total +"))

    def test_rename_is_unknown(self):
        p = "--- a/pkg/box.py\n+++ b/pkg/other.py\n@@ -1 +1 @@\n-import os\n+import sys\n"
        with self.assertRaises(Unknown):
            select(p, self.map)

    def test_unmapped_python_is_unknown(self):
        with self.assertRaises(Unknown):
            select(patch("a = 1\n", "a = 2\n", "sqlglot/new.py"), self.map)


if __name__ == "__main__":
    unittest.main()
