"""Regression cases for narrowing only when pinned dependency evidence permits it."""

import copy
import difflib
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from impact import BASELINE_COMMIT, choose_tests


SOURCE = """import math

class Calculator:
    def double(self, value):
        result = value * 2
        return result

def separate(value):
    result = value + 1
    return result
"""
ALL = {1, 2, 3, 4, 5}
PATH = "sqlglot/example.py"


def patch(before=SOURCE, after=None, path=PATH, context=3):
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True), after.splitlines(keepends=True),
        fromfile="a/" + path, tofile="b/" + path, n=context,
    ))


def evidence(source=SOURCE):
    return {
        "version": 1,
        "baseline_commit": BASELINE_COMMIT,
        "files": {
            PATH: {
                "source": source,
                "complete": True,
                "functions": {"Calculator.double": [1, 2], "separate": [3]},
                "file_dependencies": [4],
                "lines": {"5": [5]},
            }
        },
    }


class ImpactTests(unittest.TestCase):
    def setUp(self):
        self.coverage = evidence()
        self.change = patch(after=SOURCE.replace("value + 1", "value + 2"))

    def choose(self, change=None, coverage=None):
        return choose_tests(self.change if change is None else change, ALL,
                            self.coverage if coverage is None else coverage)

    def test_mapped_body_change_includes_file_dependencies(self):
        self.assertEqual(self.choose(), {3, 4})

    def test_line_evidence_can_add_tests(self):
        change = patch(after=SOURCE.replace("value * 2", "value * 3"))
        self.assertEqual(self.choose(change), {1, 2, 4, 5})

    def test_pure_insertion_uses_new_function_boundaries(self):
        change = patch(after=SOURCE.replace("    result = value + 1\n", "    result = value + 1\n    result += 1\n"), context=0)
        self.assertEqual(self.choose(change), {3, 4})

    def test_pure_deletion_uses_old_function_boundaries(self):
        change = patch(after=SOURCE.replace("    result = value + 1\n", ""), context=0)
        self.assertEqual(self.choose(change), {3, 4})

    def test_multiple_hunks_in_same_file(self):
        self.coverage["files"][PATH]["lines"] = {}
        changed = SOURCE.replace("value * 2", "value * 3").replace("value + 1", "value + 2")
        self.assertEqual(self.choose(patch(after=changed, context=0)), {1, 2, 3, 4})

    def test_unknown_file_in_multi_file_patch_falls_back(self):
        unknown = patch(after=SOURCE.replace("value + 1", "value + 2"), path="unknown.py")
        self.assertEqual(self.choose(self.change + unknown), ALL)

    def test_multiple_mapped_files_are_combined(self):
        second = "sqlglot/another.py"
        self.coverage["files"][second] = copy.deepcopy(self.coverage["files"][PATH])
        self.coverage["files"][second]["functions"]["separate"] = [5]
        second_change = patch(after=SOURCE.replace("value + 1", "value + 2"), path=second)
        self.assertEqual(self.choose(self.change + second_change), {3, 4, 5})

    def test_comment_change_is_not_skipped(self):
        changed = SOURCE.replace("value + 1", "value + 1  # different comment")
        self.assertEqual(self.choose(patch(after=changed)), {3, 4})

    def test_missing_coverage_falls_back(self):
        self.assertEqual(choose_tests(self.change, ALL), ALL)

    def test_wrong_baseline_falls_back(self):
        self.coverage["baseline_commit"] = "other"
        self.assertEqual(self.choose(), ALL)

    def test_unknown_file_falls_back(self):
        self.assertEqual(self.choose(patch(after=SOURCE.replace("+ 1", "+ 2"), path="other.py")), ALL)

    def test_incomplete_file_falls_back(self):
        self.coverage["files"][PATH]["complete"] = False
        self.assertEqual(self.choose(), ALL)

    def test_missing_function_falls_back(self):
        del self.coverage["files"][PATH]["functions"]["separate"]
        self.assertEqual(self.choose(), ALL)

    def test_unobserved_function_falls_back(self):
        self.coverage["files"][PATH]["functions"]["separate"] = []
        self.assertEqual(self.choose(), ALL)

    def test_unknown_dependency_id_falls_back(self):
        self.coverage["files"][PATH]["functions"]["separate"] = [99]
        self.assertEqual(self.choose(), ALL)

    def test_missing_collection_dependency_evidence_falls_back(self):
        del self.coverage["files"][PATH]["file_dependencies"]
        self.assertEqual(self.choose(), ALL)

    def test_syntax_error_falls_back(self):
        self.assertEqual(self.choose(patch(after=SOURCE.replace("value + 1", "value +"))), ALL)

    def test_new_import_inside_function_falls_back(self):
        changed = SOURCE.replace("    result = value + 1", "    import os\n    result = value + 1")
        self.assertEqual(self.choose(patch(after=changed)), ALL)

    def test_module_scope_change_falls_back(self):
        self.assertEqual(self.choose(patch(after=SOURCE.replace("import math", "import sys"))), ALL)

    def test_signature_change_falls_back(self):
        self.assertEqual(self.choose(patch(after=SOURCE.replace("def separate(value):", "def separate(value=0):"))), ALL)

    def test_added_function_falls_back(self):
        self.assertEqual(self.choose(patch(after=SOURCE + "\ndef added():\n    return 1\n")), ALL)

    def test_changed_class_attribute_falls_back(self):
        changed = SOURCE.replace("class Calculator:\n", "class Calculator:\n    scale = 2\n")
        self.assertEqual(self.choose(patch(after=changed)), ALL)

    def test_context_must_match_exact_baseline(self):
        self.coverage["files"][PATH]["source"] = SOURCE.replace("+ 1", "+ 99")
        self.assertEqual(self.choose(), ALL)

    def test_truncated_hunk_falls_back(self):
        self.assertEqual(self.choose(self.change.rsplit("\n", 2)[0] + "\n"), ALL)

    def test_incorrect_hunk_counts_fall_back(self):
        self.assertEqual(self.choose(self.change.replace("@@ -6,5 +6,5 @@", "@@ -6,50 +6,50 @@")), ALL)

    def test_unknown_metadata_and_empty_patch_fall_back(self):
        for text in ("", "Binary files a/x and b/x differ\n", "rename from x\n", "garbage\n", "@@ -1 +1 @@\n-a\n+b\n"):
            with self.subTest(text=text):
                self.assertEqual(self.choose(text), ALL)

    def test_git_headers_are_accepted(self):
        self.assertEqual(self.choose("diff --git a/sqlglot/example.py b/sqlglot/example.py\nindex 123..456 100644\n" + self.change), {3, 4})

    def test_new_deleted_renamed_and_traversal_files_fall_back(self):
        for change in (
            self.change.replace("--- a/" + PATH, "--- /dev/null"),
            self.change.replace("+++ b/" + PATH, "+++ /dev/null"),
            self.change.replace("+++ b/" + PATH, "+++ b/renamed.py"),
            self.change.replace(PATH, "../escape.py"),
        ):
            with self.subTest(change=change):
                self.assertEqual(self.choose(change), ALL)

    def test_no_final_newline_marker_is_applied(self):
        before = "def separate(value):\n    return value + 1"
        change = "--- a/sqlglot/example.py\n+++ b/sqlglot/example.py\n@@ -1,2 +1,2 @@\n def separate(value):\n-    return value + 1\n\\ No newline at end of file\n+    return value + 2\n\\ No newline at end of file\n"
        self.assertEqual(self.choose(change, evidence(before)), {3, 4})

    def test_inputs_are_not_mutated_and_generator_ids_work(self):
        original = copy.deepcopy(self.coverage)
        self.assertEqual(choose_tests(self.change, iter(ALL), self.coverage), {3, 4})
        self.assertEqual(self.coverage, original)

    def test_empty_catalog_stays_empty(self):
        self.assertEqual(choose_tests("not a diff", [], None), set())


if __name__ == "__main__":
    unittest.main()
