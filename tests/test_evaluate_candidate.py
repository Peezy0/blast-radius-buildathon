"""Exercise real candidate subprocess failures and trusted local scoring."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.evaluate_candidate import TRUSTED_SCORER, evaluate, source_manifest


VALID = '''import argparse, json
parser=argparse.ArgumentParser()
parser.add_argument('--data-dir')
args=parser.parse_args()
print(json.dumps({'commit':'change','workers':[[0]]}))
'''


class EvaluateCandidateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.candidate = self.root / "candidate"
        self.candidate.mkdir()
        (self.candidate / "src").mkdir()
        (self.candidate / "src" / "library.py").write_text("value=1\n")
        self.write_candidate(VALID)
        self.data = self.root / "data"
        self.data.mkdir()
        (self.data / "commits").mkdir()
        (self.data / "commits" / "change.patch").write_text("patch contents\n")
        (self.data / "tests.json").write_text(json.dumps({"tests": [
            {"number": 0, "id": "tests/test_a.py::test_a", "file": "tests/test_a.py", "seconds": .01}
        ]}))
        self.answers = self.root / "private-answers.json"
        self.answers.write_text(json.dumps({"commits": {"change": {"failing": [0]}}}))

    def write_candidate(self, text):
        (self.candidate / "selector.py").write_text(text)

    def evaluate(self, **kwargs):
        return evaluate(self.candidate, self.data, self.answers, TRUSTED_SCORER,
                        **kwargs)

    def test_valid_repeats_record_exact_trusted_score(self):
        result = self.evaluate()
        self.assertTrue(result["verified"], result)
        self.assertEqual(result["verification_scope"], "local_subprocess")
        self.assertTrue(result["deterministic"])
        self.assertEqual(result["simulated_milliseconds"], 2010)
        self.assertEqual(result["misses"], 0)
        self.assertEqual(len(result["runs"]), 3)
        self.assertEqual(len(result["source_sha256"]), 64)
        self.assertIn("selector.py", result["source_manifest"])
        for run in result["runs"]:
            self.assertEqual(run["output_validation"]["missed_commits"], [])
            self.assertTrue(run["source_unchanged"])
            self.assertTrue(run["inputs_unchanged"])
            self.assertGreater(run["wall_seconds"], 0)
        if hasattr(os, "wait4"):
            self.assertEqual(result["cpu_measurement"], "wait4")
            self.assertGreater(result["median_total_microseconds"], 2_010_000)

    def test_missing_failing_test_is_valid_but_penalized(self):
        self.write_candidate("print('{\"commit\":\"change\",\"workers\":[]}')\n")
        result = self.evaluate()
        self.assertTrue(result["verified"], result)
        self.assertEqual(result["misses"], 1)
        self.assertEqual(result["simulated_milliseconds"], 300_000)
        self.assertEqual(result["runs"][0]["output_validation"]["missed_commits"], ["change"])

    def test_crash_is_rejected_without_median(self):
        self.write_candidate("raise RuntimeError('broken candidate')\n")
        result = self.evaluate()
        self.assertFalse(result["ok"])
        self.assertIn("nonzero_exit", result["reasons"])
        self.assertEqual(len(result["runs"]), 1)
        self.assertIsNone(result["median_total_microseconds"])

    def test_timeout_is_bounded_and_rejected(self):
        self.write_candidate("import time\ntime.sleep(10)\n")
        result = self.evaluate(timeout=.05)
        self.assertIn("timeout", result["reasons"])
        self.assertFalse(result["ok"])
        self.assertLess(result["runs"][0]["wall_seconds"], 3)

    def test_stdout_pollution_is_officially_rejected(self):
        self.write_candidate(VALID + "print('debug log')\n")
        result = self.evaluate()
        self.assertIn("invalid_output:not_json", result["reasons"])
        self.assertEqual(result["runs"][0]["output_validation"]["line"], 2)

    def test_duplicate_test_is_officially_rejected(self):
        self.write_candidate("print('{\"commit\":\"change\",\"workers\":[[0,0]]}')\n")
        result = self.evaluate()
        self.assertIn("invalid_output:repeated_test", result["reasons"])

    def test_stdout_limit_prevents_unbounded_capture(self):
        self.write_candidate("import sys\nsys.stdout.write('x'*3000000)\n")
        result = self.evaluate()
        self.assertIn("stdout_limit", result["reasons"])
        self.assertFalse(result["ok"])
        self.assertLess(result["runs"][0]["stdout_bytes"], 2_100_000)

    def test_source_mutation_is_rejected(self):
        self.write_candidate(VALID + "from pathlib import Path\nPath('src/library.py').write_text('changed=2')\n")
        result = self.evaluate()
        self.assertIn("source_mutation", result["reasons"])
        self.assertFalse(result["runs"][0]["source_unchanged"])

    def test_new_python_source_file_is_mutation(self):
        self.write_candidate(VALID + "from pathlib import Path\nPath('src/new.py').write_text('changed=2')\n")
        self.assertIn("source_mutation", self.evaluate()["reasons"])

    def test_trusted_input_mutation_is_rejected(self):
        self.write_candidate(VALID + "from pathlib import Path\nPath(args.data_dir,'commits','change.patch').write_text('tampered')\n")
        result = self.evaluate()
        self.assertIn("trusted_input_mutation", result["reasons"])

    def test_different_valid_outputs_are_nondeterministic(self):
        self.write_candidate("""import json
from pathlib import Path
counter = Path('../counter')
value = int(counter.read_text()) if counter.exists() else 0
counter.write_text(str(value+1))
print(json.dumps({'commit':'change','workers':[[0]] if value%2 else []}))
""")
        result = self.evaluate()
        self.assertFalse(result["deterministic"])
        self.assertIn("nondeterministic_output", result["reasons"])
        self.assertEqual(len(result["runs"]), 3)

    def test_answers_and_host_environment_not_sent_to_candidate(self):
        self.write_candidate(VALID + """import os, sys
assert 'PRIVATE_ANSWERS_PATH' not in os.environ
assert all('answers' not in value for value in sys.argv)
""")
        with patch.dict(os.environ, {"PRIVATE_ANSWERS_PATH": str(self.answers)}):
            result = self.evaluate()
        self.assertTrue(result["ok"], result)

    def test_candidate_provided_scorer_is_rejected(self):
        fake = self.candidate / "score.py"
        fake.write_text("def evaluate_run(*args): return {'ok':True}\n")
        result = evaluate(self.candidate, self.data, self.answers, fake)
        self.assertFalse(result["ok"])
        self.assertIn("trusted checkpoint", result["reasons"][0])

    def test_at_least_three_repeats_required(self):
        result = self.evaluate(repeats=2)
        self.assertFalse(result["ok"])
        self.assertEqual(result["runs"], [])

    def test_answer_dataset_mismatch_rejects_before_run(self):
        self.answers.write_text('{"commits":{}}')
        result = self.evaluate()
        self.assertFalse(result["ok"])
        self.assertEqual(result["runs"], [])

    def test_source_manifest_includes_dockerfile(self):
        first = source_manifest(self.candidate)
        (self.candidate / "Dockerfile").write_text("FROM python\n")
        second = source_manifest(self.candidate)
        self.assertNotEqual(first, second)
        self.assertIn("Dockerfile", second)

    @unittest.skipUnless(os.name == "posix", "symlink support required")
    def test_source_symlink_is_rejected(self):
        (self.candidate / "src" / "linked.py").symlink_to(self.candidate / "selector.py")
        result = self.evaluate()
        self.assertFalse(result["ok"])
        self.assertIn("symlinks", result["reasons"][0])

    @unittest.skipUnless(os.name == "posix", "symlink support required")
    def test_source_directory_symlink_is_rejected(self):
        library = self.candidate / "src" / "library.py"
        library.unlink()
        (self.candidate / "src").rmdir()
        external = self.root / "external-src"
        external.mkdir()
        (self.candidate / "src").symlink_to(external, target_is_directory=True)
        result = self.evaluate()
        self.assertFalse(result["ok"])
        self.assertIn("symlinks", result["reasons"][0])

    def test_cli_reports_json_and_exit_status(self):
        result = subprocess.run([
            sys.executable, str(ROOT / "tools" / "evaluate_candidate.py"),
            str(self.candidate), "--data-dir", str(self.data), "--answers", str(self.answers),
        ], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["verified"])


if __name__ == "__main__":
    unittest.main()
