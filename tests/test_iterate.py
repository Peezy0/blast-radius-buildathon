"""Exercise evidence gating with isolated candidates and trusted test copies."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest

from tools.iterate import IterationError, TRUSTED_TEST_NAMES, run_iteration


class IterationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.trusted = self.root / "trusted"
        (self.trusted / "tests").mkdir(parents=True)
        for index, name in enumerate(TRUSTED_TEST_NAMES):
            (self.trusted / "tests" / name).write_text(
                "import unittest\nimport selector\n"
                f"class Trusted{index}(unittest.TestCase):\n"
                " def test_candidate_import(self):\n"
                "  self.assertGreaterEqual(selector.VALUE, 1)\n"
            )
        # Importing this root selector would fail, proving the gate imports the
        # candidate snapshot rather than quietly testing the original project.
        (self.trusted / "selector.py").write_text("VALUE = -99\n")
        self.data = self.root / "data"
        (self.data / "commits").mkdir(parents=True)
        (self.data / "tests.json").write_text('{"tests": []}\n')
        (self.data / "commits" / "a.patch").write_text("a patch\n")
        self.answers = self.root / "answers.json"
        self.answers.write_text('{"commits":{"a":{"failing":[]}}}\n')
        self.scorer = self.root / "score.py"
        self.scorer.write_text("# trusted fake scorer identity\n")
        self.base = self.candidate("baseline", "VALUE = 1\n# SCORE=10000000\n")
        self.output = self.root / "run"
        self.evaluated_source = []

    def candidate(self, name, source):
        path = self.root / name
        path.mkdir()
        (path / "selector.py").write_text(source)
        return path

    def evaluation(self, total=10_000_000, misses=(), *, deterministic=True):
        runs = []
        for index in range(3):
            runs.append({
                "returncode": 0, "error": None, "source_unchanged": True,
                "cpu_microseconds": 100, "total_microseconds": total,
                "stdout_sha256": "output" if deterministic else str(index),
                "output_validation": {"ok": True, "simulated_milliseconds": total // 1000,
                                      "missed_commits": list(misses)},
            })
        return {"ok": True, "verified": True, "verification_scope": "local_subprocess",
                "deterministic": deterministic, "runs": runs,
                "median_total_microseconds": total, "misses": len(misses)}

    def evaluator(self, directory, data_dir, answers_path, scorer_path, **options):
        self.assertEqual(options["repeats"], 3)
        self.assertGreater(options["timeout"], 0)
        self.assertFalse((directory / "official").exists())
        self.assertFalse((directory / "tests").exists())
        source = (directory / "selector.py").read_text()
        self.evaluated_source.append(source)
        score = int(source.split("# SCORE=")[1].splitlines()[0])
        return self.evaluation(score)

    def run_queue(self, candidates=(), evaluator=None, **kwargs):
        return run_iteration(
            self.base, candidates, self.output, self.data, self.answers, self.scorer,
            timeout=2, trusted_root=self.trusted, evaluator=evaluator or self.evaluator,
            clock=lambda: datetime(2026, 11, 20, 23, tzinfo=timezone.utc), **kwargs,
        )

    def ledger(self):
        return [json.loads(line) for line in (self.output / "evidence.jsonl").read_text().splitlines()]

    def test_promotes_verified_improvement_retains_snapshot_and_atomic_record(self):
        candidate = self.candidate("better", "VALUE = 2\n# SCORE=7000000\n")
        result = self.run_queue([candidate])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["promoted"], 1)
        champion = json.loads((self.output / "champion.json").read_text())
        self.assertEqual(champion["candidate_dir"], str(candidate))
        self.assertEqual(champion["median_total_microseconds"], 7_000_000)
        self.assertFalse(champion["cloud_execution_verified"])
        retained = Path(champion["retained_source_dir"])
        self.assertEqual((retained / "selector.py").read_text(), (candidate / "selector.py").read_text())
        self.assertEqual(len(self.evaluated_source), 2)
        self.assertFalse((self.output / ".iteration.lock").exists())

    def test_duplicate_source_is_cached_only_within_this_run(self):
        repeated = self.candidate("repeat", (self.base / "selector.py").read_text())
        result = self.run_queue([repeated, repeated])
        self.assertEqual(result["duplicates"], 2)
        self.assertEqual(len(self.evaluated_source), 1)
        self.assertEqual(result["champion"]["candidate_dir"], str(self.base))

    def test_rejects_worse_and_below_margin_candidates(self):
        worse = self.candidate("worse", "VALUE = 2\n# SCORE=11000000\n")
        noise = self.candidate("noise", "VALUE = 3\n# SCORE=9500000\n")
        result = self.run_queue([worse, noise])
        self.assertEqual(result["promoted"], 0)
        self.assertEqual(result["rejected"], 2)
        self.assertEqual(result["champion"]["candidate_dir"], str(self.base))

    def test_candidate_cannot_replace_trusted_tests_with_its_own(self):
        candidate = self.candidate("bad", "VALUE = -1\n# SCORE=1000000\n")
        (candidate / "tests").mkdir()
        (candidate / "tests/test_fake.py").write_text("import unittest\nclass T(unittest.TestCase):\n def test_ok(self): pass\n")
        result = self.run_queue([candidate])
        self.assertEqual(result["promoted"], 0)
        self.assertEqual(len(self.evaluated_source), 1)
        evidence = [item for item in self.ledger() if item["event"] == "candidate"][0]
        self.assertFalse(evidence["unit_tests"]["ok"])

    def test_detects_malicious_rewrite_of_copied_trusted_tests(self):
        candidate = self.candidate(
            "malicious", "from pathlib import Path\nVALUE = 2\n# SCORE=1000000\n"
            "for p in (Path(__file__).parent / 'tests').glob('test_*.py'):\n"
            " p.write_text('import unittest\\nclass T(unittest.TestCase):\\n def test_ok(self): pass\\n')\n",
        )
        result = self.run_queue([candidate])
        self.assertEqual(result["promoted"], 0)
        self.assertEqual(len(self.evaluated_source), 1)
        evidence = [item for item in self.ledger() if item["event"] == "candidate"][0]
        self.assertIn("trusted unit test gate failed", evidence["reasons"])

    def test_source_mutation_during_evaluation_is_rejected(self):
        candidate = self.candidate("mutates", "VALUE = 2\n# SCORE=1000000\n")
        def evaluate(directory, *args, **kwargs):
            result = self.evaluator(directory, *args, **kwargs)
            if "VALUE = 2" in (directory / "selector.py").read_text():
                (directory / "selector.py").write_text("VALUE = 900\n")
            return result
        result = self.run_queue([candidate], evaluator=evaluate)
        self.assertEqual(result["promoted"], 0)
        self.assertEqual((candidate / "selector.py").read_text(), "VALUE = 2\n# SCORE=1000000\n")

    def test_original_source_mutation_during_evaluation_is_rejected(self):
        candidate = self.candidate("original_mutates", "VALUE = 2\n# SCORE=1000000\n")
        def evaluate(directory, *args, **kwargs):
            result = self.evaluator(directory, *args, **kwargs)
            if "VALUE = 2" in (directory / "selector.py").read_text():
                (candidate / "selector.py").write_text("VALUE = 3\n")
            return result
        result = self.run_queue([candidate], evaluator=evaluate)
        self.assertEqual(result["promoted"], 0)
        self.assertEqual(result["champion"]["candidate_dir"], str(self.base))

    def test_rejects_newly_missed_case_even_when_miss_count_is_equal(self):
        candidate = self.candidate("different_miss", "VALUE = 2\n# SCORE=1000000\n")
        def evaluate(directory, *args, **kwargs):
            result = self.evaluator(directory, *args, **kwargs)
            misses = ["a"] if "VALUE = 1" in (directory / "selector.py").read_text() else ["b"]
            for run in result["runs"]:
                run["output_validation"]["missed_commits"] = misses
            result["misses"] = 1
            return result
        result = self.run_queue([candidate], evaluator=evaluate)
        self.assertEqual(result["promoted"], 0)
        evidence = [item for item in self.ledger() if item["event"] == "candidate"][0]
        self.assertIn("candidate introduced a newly missed commit", evidence["reasons"])

    def test_rejects_non_deterministic_output(self):
        candidate = self.candidate("nondeterministic", "VALUE = 2\n# SCORE=1000000\n")
        def evaluate(directory, *args, **kwargs):
            if "VALUE = 2" in (directory / "selector.py").read_text():
                return self.evaluation(1_000_000, deterministic=False)
            return self.evaluator(directory, *args, **kwargs)
        result = self.run_queue([candidate], evaluator=evaluate)
        self.assertEqual(result["promoted"], 0)

    def test_timeout_evidence_retains_baseline(self):
        candidate = self.candidate("timeout", "VALUE = 2\n# SCORE=1000000\n")
        def evaluate(directory, *args, **kwargs):
            result = self.evaluator(directory, *args, **kwargs)
            if "VALUE = 2" in (directory / "selector.py").read_text():
                result.update(ok=False, verified=False, deterministic=False)
                result["runs"] = [dict(result["runs"][0], error="timeout", returncode=-9)]
            return result
        result = self.run_queue([candidate], evaluator=evaluate)
        self.assertEqual(result["promoted"], 0)
        self.assertEqual(result["champion"]["candidate_dir"], str(self.base))

    def test_actual_trusted_unit_test_timeout_is_bounded(self):
        candidate = self.candidate("hangs", "import time\ntime.sleep(20)\nVALUE = 2\n# SCORE=1000000\n")
        # Baseline's tiny tests finish under the same 0.2-second gate budget.
        result = run_iteration(self.base, [candidate], self.output, self.data, self.answers,
                               self.scorer, timeout=0.2, trusted_root=self.trusted,
                               evaluator=self.evaluator,
                               clock=lambda: datetime(2026, 11, 20, tzinfo=timezone.utc))
        self.assertEqual(result["promoted"], 0)
        evidence = [item for item in self.ledger() if item["event"] == "candidate"][0]
        self.assertTrue(evidence["unit_tests"]["timed_out"])

    def test_baseline_failure_stops_before_candidates(self):
        (self.base / "selector.py").write_text("VALUE = -1\n# SCORE=10000000\n")
        candidate = self.candidate("unused", "VALUE = 2\n# SCORE=1000000\n")
        result = self.run_queue([candidate])
        self.assertEqual(result["status"], "baseline_failed")
        self.assertIsNone(result["champion"])
        self.assertEqual(self.evaluated_source, [])
        self.assertFalse((self.output / "champion.json").exists())

    def test_deadline_refuses_all_work(self):
        result = self.run_queue(deadline="2026-11-20T22:00:00Z")
        self.assertEqual(result["status"], "deadline_reached")
        self.assertEqual(self.evaluated_source, [])

    def test_deadline_during_evaluation_prevents_promotion(self):
        moment = [datetime(2026, 11, 20, tzinfo=timezone.utc)]
        def evaluate(directory, *args, **kwargs):
            result = self.evaluator(directory, *args, **kwargs)
            moment[0] = datetime(2026, 11, 23, tzinfo=timezone.utc)
            return result
        result = run_iteration(self.base, [], self.output, self.data, self.answers,
                               self.scorer, timeout=2, trusted_root=self.trusted,
                               evaluator=evaluate, clock=lambda: moment[0])
        self.assertEqual(result["status"], "deadline_reached")
        self.assertIsNone(result["champion"])
        self.assertFalse((self.output / "champion.json").exists())

    def test_inconsistent_median_evidence_is_rejected(self):
        candidate = self.candidate("wrong_median", "VALUE = 2\n# SCORE=1000000\n")
        def evaluate(directory, *args, **kwargs):
            result = self.evaluator(directory, *args, **kwargs)
            if "VALUE = 2" in (directory / "selector.py").read_text():
                for run in result["runs"]:
                    run["total_microseconds"] = 12_000_000
            return result
        result = self.run_queue([candidate], evaluator=evaluate)
        self.assertEqual(result["promoted"], 0)

    def test_symlink_source_is_rejected(self):
        candidate = self.candidate("symlink", "VALUE = 2\n# SCORE=1000000\n")
        (candidate / "src").mkdir()
        (candidate / "src/helper.py").symlink_to(self.base / "selector.py")
        result = self.run_queue([candidate])
        self.assertEqual(result["promoted"], 0)
        self.assertEqual(len(self.evaluated_source), 1)

    def test_exclusive_lock_and_existing_run_are_refused(self):
        self.output.mkdir()
        lock = self.output / ".iteration.lock"
        lock.write_text("other process")
        with self.assertRaises(IterationError):
            self.run_queue()
        self.assertTrue(lock.exists())
        lock.unlink()
        self.run_queue()
        with self.assertRaises(IterationError):
            self.run_queue()
        self.assertFalse(lock.exists())

    def test_noise_spread_requires_larger_improvement(self):
        candidate = self.candidate("noisy", "VALUE = 2\n# SCORE=8000000\n")
        def evaluate(directory, *args, **kwargs):
            result = self.evaluator(directory, *args, **kwargs)
            if "VALUE = 1" in (directory / "selector.py").read_text():
                result["runs"][0]["total_microseconds"] = 7_000_000
            return result
        result = self.run_queue([candidate], evaluator=evaluate)
        self.assertEqual(result["promoted"], 0)


if __name__ == "__main__":
    unittest.main()
