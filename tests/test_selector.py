"""Integration checks for the actual selector process and its output protocol."""

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import selector
from src.impact import BASELINE_COMMIT


def catalog(durations=(3.0, 2.0, 1.0)):
    return {"tests": [
        {"number": i, "id": f"tests/test_example.py::test_{i}",
         "file": "tests/test_example.py", "seconds": seconds}
        for i, seconds in enumerate(durations)
    ]}


class SelectorIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        (self.data / "commits").mkdir()
        self.write_catalog(catalog())
        (self.data / "commits" / "z-last.patch").write_text("unsupported patch")
        (self.data / "commits" / "a-first.patch").write_text("unsupported patch")

    def write_catalog(self, value):
        (self.data / "tests.json").write_text(json.dumps(value))

    def run_cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "selector.py"), "--data-dir", str(self.data), *args],
            capture_output=True, timeout=10,
        )

    def test_process_output_is_strict_and_complete(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r["commit"] for r in rows], ["a-first", "z-last"])
        for row in rows:
            self.assertEqual(set(row), {"commit", "workers"})
            self.assertLessEqual(len(row["workers"]), 8)
            values = [test for worker in row["workers"] for test in worker]
            self.assertEqual(sorted(values), [0, 1, 2])
        stats = json.loads(result.stderr)
        self.assertEqual(stats["schedules"], 1)
        self.assertEqual(stats["output_bytes"], len(result.stdout))

    def test_repeated_invocations_are_deterministic(self):
        self.assertEqual(self.run_cli().stdout, self.run_cli().stdout)

    def test_selection_schedule_is_computed_once(self):
        original = selector.optimize
        with patch.object(selector, "optimize", wraps=original) as mock:
            selector.generate_output(self.data)
            self.assertEqual(mock.call_count, 1)

    def test_default_does_not_read_patches(self):
        (self.data / "commits" / "a-first.patch").write_bytes(b"\xff\x00")
        self.assertEqual(self.run_cli().returncode, 0)

    def test_missing_coverage_keeps_full_suite(self):
        self.assertEqual(json.loads(self.run_cli().stderr)["narrowed"], 0)

    def test_explicit_missing_coverage_errors_without_stdout(self):
        result = self.run_cli("--coverage", str(self.data / "missing.json"))
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"selector:", result.stderr)

    def test_incomplete_map_falls_back(self):
        coverage = self.data / "coverage.json"
        coverage.write_text("{}")
        result = self.run_cli("--coverage", str(coverage))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stderr)["narrowed"], 0)

    def test_complete_baseline_map_can_narrow(self):
        source = "def parse(value):\n    return value + 1\n"
        patch_text = (
            "--- a/sqlglot/example.py\n+++ b/sqlglot/example.py\n"
            "@@ -1,2 +1,2 @@\n def parse(value):\n"
            "-    return value + 1\n+    return value + 2\n"
        )
        for path in (self.data / "commits").glob("*.patch"):
            path.write_text(patch_text)
        coverage = self.data / "coverage.json"
        coverage.write_text(json.dumps({
            "version": 1, "baseline_commit": BASELINE_COMMIT,
            "files": {"sqlglot/example.py": {
                "source": source, "complete": True,
                "functions": {"parse": [1]}, "file_dependencies": [],
            }},
        }))
        result = self.run_cli("--coverage", str(coverage))
        self.assertEqual(result.returncode, 0, result.stderr)
        for line in result.stdout.splitlines():
            self.assertEqual(json.loads(line)["workers"], [[1]])

    def test_empty_catalog_and_commit_set(self):
        self.write_catalog(catalog(()))
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(json.loads(line)["workers"] == [] for line in result.stdout.splitlines()))
        for path in (self.data / "commits").glob("*.patch"):
            path.unlink()
        self.assertEqual(self.run_cli().stdout, b"")

    def test_output_limit_fails_atomically(self):
        with patch.object(selector, "MAX_OUTPUT_BYTES", 1):
            with self.assertRaisesRegex(ValueError, "output exceeds"):
                selector.generate_output(self.data)

    def test_missing_commits_directory(self):
        for path in (self.data / "commits").glob("*.patch"):
            path.unlink()
        (self.data / "commits").rmdir()
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stdout, b"")

    def test_invalid_catalogs_reject_before_output(self):
        values = [[], {}, {"tests": {}}, {"tests": [True]}]
        for field, value in [
            ("number", True), ("number", 2), ("seconds", -1),
            ("seconds", float("inf")), ("seconds", True),
            ("id", ""), ("file", None),
        ]:
            item = catalog()
            item["tests"][0][field] = value
            values.append(item)
        duplicate = catalog()
        duplicate["tests"][1]["id"] = duplicate["tests"][0]["id"]
        values.append(duplicate)
        for value in values:
            with self.subTest(value=value):
                self.write_catalog(value)
                result = self.run_cli()
                self.assertEqual(result.returncode, 2)
                self.assertEqual(result.stdout, b"")

    def test_duplicate_json_keys_rejected(self):
        (self.data / "tests.json").write_text('{"tests":[],"tests":[]}')
        result = self.run_cli()
        self.assertEqual(result.returncode, 2)
        self.assertIn(b"duplicate JSON key", result.stderr)

    def test_official_sample_passes_scorer_without_misses(self):
        sample = ROOT / "official" / "sample"
        score_path = ROOT / "official" / "score.py"
        if not sample.exists() or not score_path.exists():
            self.skipTest("official sample kit unavailable")
        spec = importlib.util.spec_from_file_location("official_score", score_path)
        scorer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scorer)
        output, stats = selector.generate_output(sample)
        tests = json.loads((sample / "tests.json").read_text())["tests"]
        answers = json.loads((sample / "answers.json").read_text())["commits"]
        result = scorer.score_run(output, tests, answers)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["misses"], 0, result)
        self.assertLess(result["simulated_milliseconds"], 1_614_100)
        self.assertEqual(stats["commits"], 30)


if __name__ == "__main__":
    unittest.main()
