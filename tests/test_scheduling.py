"""Validate worker costs, deterministic layouts, and heuristic quality."""

import itertools
import math
import random
import unittest

from src.scheduling import fill_slack, optimize, schedule_cost


def exact_cost(durations, maximum=8):
    """Enumerate unlabeled worker partitions for tiny validation cases."""
    jobs = sorted(durations.values(), reverse=True)
    if not jobs:
        return 0.0
    best = sum(jobs) + 2.0
    loads = []

    def visit(position):
        nonlocal best
        if position == len(jobs):
            best = min(best, max(loads) + 2 * len(loads))
            return
        if loads and max(loads) + 2 * len(loads) >= best:
            return
        seconds = jobs[position]
        previous_loads = set()
        for index in range(len(loads)):
            old = loads[index]
            if old in previous_loads:
                continue
            previous_loads.add(old)
            loads[index] += seconds
            visit(position + 1)
            loads[index] = old
        if len(loads) < maximum:
            loads.append(seconds)
            visit(position + 1)
            loads.pop()

    visit(0)
    return best


def reference_lpt_cost(durations, maximum=8):
    """Independent unpruned reference, to check the optimization shortcut."""
    if not durations:
        return 0.0
    durations = {test: math.floor(seconds * 1000 + 0.5) for test, seconds in durations.items()}
    ordered = sorted(durations, key=lambda test: (-durations[test], test))
    costs = []
    for count in range(1, min(maximum, len(ordered)) + 1):
        workers = [[] for _ in range(count)]
        for test in ordered:
            destination = min(
                range(count),
                key=lambda index: (sum(durations[t] for t in workers[index]), index),
            )
            workers[destination].append(test)
        workers = [worker for worker in workers if worker]
        costs.append(max(sum(durations[t] for t in w) for w in workers) + 2000 * len(workers))
    return min(costs) / 1000


class SchedulingTests(unittest.TestCase):
    def assertValid(self, durations, workers, selected=None, limit=8):
        expected = set(durations if selected is None else selected)
        flattened = list(itertools.chain.from_iterable(workers))
        self.assertEqual(set(flattened), expected)
        self.assertEqual(len(flattened), len(expected))
        self.assertLessEqual(len(workers), limit)
        self.assertTrue(all(workers))

    def test_empty_selection_means_no_workers(self):
        self.assertEqual(optimize({}), [])
        self.assertEqual(optimize({1: 4.0}, []), [])
        self.assertEqual(schedule_cost({}, []), 0)

    def test_single_test_and_duplicate_selection(self):
        self.assertEqual(optimize({2: 5.0}, [2, 2]), [[2]])
        self.assertEqual(schedule_cost({2: 5.0}, [[2]]), 7.0)

    def test_worker_overhead_can_favor_serial_execution(self):
        durations = {index: 0.1 for index in range(12)}
        workers = optimize(durations)
        self.assertEqual(len(workers), 1)
        self.assertAlmostEqual(schedule_cost(durations, workers), 3.2)

    def test_four_workers_beat_eight_for_eight_equal_tests(self):
        durations = dict.fromkeys(range(8), 6.0)
        workers = optimize(durations)
        self.assertEqual(len(workers), 4)
        self.assertEqual(schedule_cost(durations, workers), 20.0)

    def test_one_long_test_does_not_create_useless_workers(self):
        durations = {0: 100.0, 1: 1.0, 2: 1.0, 3: 1.0}
        workers = optimize(durations)
        self.assertEqual(len(workers), 2)
        self.assertEqual(schedule_cost(durations, workers), 104.0)

    def test_all_zero_durations_use_one_worker(self):
        durations = dict.fromkeys(range(20), 0.0)
        workers = optimize(durations)
        self.assertEqual(len(workers), 1)
        self.assertEqual(schedule_cost(durations, workers), 2.0)
        self.assertValid(durations, workers)

    def test_input_order_does_not_change_layout(self):
        durations = {8: 3.0, 1: 7.0, 4: 7.0, 2: 2.0, 7: 3.0}
        before = dict(durations)
        result = optimize(durations, [8, 1, 4, 2, 7, 1])
        self.assertEqual(result, optimize(dict(reversed(list(durations.items()))), reversed(durations)))
        self.assertEqual(durations, before)
        self.assertValid(durations, result)

    def test_worker_limit_and_selection(self):
        durations = {index: index + 1 for index in range(30)}
        selected = list(range(0, 30, 2))
        workers = optimize(durations, selected, max_workers=2)
        self.assertValid(durations, workers, selected, limit=2)

    def test_small_random_cases_against_exact_partitions(self):
        generator = random.Random(183)
        for _ in range(90):
            size = generator.randint(1, 7)
            durations = {index: float(generator.randint(0, 25)) for index in range(size)}
            maximum = generator.randint(1, 8)
            with self.subTest(durations=durations, maximum=maximum):
                workers = optimize(durations, max_workers=maximum)
                actual = schedule_cost(durations, workers)
                optimum = exact_cost(durations, maximum)
                self.assertGreaterEqual(actual + 1e-9, optimum)
                # The global optimum's worker count is among our candidates;
                # LPT's makespan bound gives this looser objective-cost bound.
                self.assertLessEqual(actual, 4 * optimum / 3 + 1e-9)
                self.assertEqual(actual, reference_lpt_cost(durations, maximum))
                self.assertValid(durations, workers, limit=maximum)

    def test_fractional_durations_match_unpruned_reference(self):
        generator = random.Random(82)
        for _ in range(40):
            durations = {index: generator.random() * 9 for index in range(32)}
            actual = schedule_cost(durations, optimize(durations))
            self.assertAlmostEqual(actual, reference_lpt_cost(durations), places=10)

    def test_half_millisecond_conversion_matches_official_half_up(self):
        durations = {0: 0.0005, 1: 0.0015, 2: 0.0025, 3: 0.00049}
        self.assertEqual(schedule_cost(durations, [[0, 1, 2, 3]]), 2.006)
        self.assertEqual(optimize(durations), [[2, 1, 0, 3]])

    def test_submillisecond_raw_order_does_not_change_recorded_order(self):
        durations = {0: 0.0011, 1: 0.0014}
        self.assertEqual(optimize(durations), [[0, 1]])

    def test_cost_matches_official_scorer_on_catalog_subsets(self):
        # Import the unmodified official implementation only for validation.
        import importlib.util
        from pathlib import Path
        root = Path(__file__).resolve().parents[1]
        official = root / "official" / "score.py"
        if not official.exists():
            self.skipTest("official kit is not present")
        specification = importlib.util.spec_from_file_location("official_score", official)
        module = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(module)
        import json
        tests = json.loads((root / "official/sample/tests.json").read_text())["tests"]
        durations = {test["number"]: test["seconds"] for test in tests}
        for selected in [[], list(durations), list(durations)[::5]]:
            workers = optimize(durations, selected)
            payload = json.dumps({"commit": "validation", "workers": workers}).encode()
            result = module.evaluate_run(payload, tests, {"validation": {"failing": []}})
            self.assertTrue(result["ok"])
            self.assertEqual(round(schedule_cost(durations, workers) * 1000), result["simulated_milliseconds"])

    def test_reject_invalid_durations(self):
        for bad in [-1, math.nan, math.inf, -math.inf]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                optimize({1: bad})
        for bad in [True, "1", None, 1 + 2j]:
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                optimize({1: bad})
        with self.assertRaises(ValueError):
            optimize({1: 1e308, 2: 1e308})

    def test_reject_invalid_ids_and_limits(self):
        for bad in [True, -1, "1", 1.5]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                optimize({bad: 1})
        with self.assertRaises(KeyError):
            optimize({1: 1}, [9])
        for bad in [0, -1, 9]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                optimize({}, max_workers=bad)
        for bad in [True, 2.5]:
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                optimize({}, max_workers=bad)


class SlackTests(unittest.TestCase):
    def test_fills_slack_without_cost_increase_or_input_mutation(self):
        durations = {0: 10.0, 1: 6.0, 2: 3.0, 3: 1.0, 4: 2.0}
        original = [[0], [1]]
        filled = fill_slack(durations, original, [2, 2, 0, 3, 4])
        self.assertEqual(filled, [[0], [1, 2, 3]])
        self.assertEqual(original, [[0], [1]])
        self.assertEqual(schedule_cost(durations, filled), schedule_cost(durations, original))

    def test_default_candidate_order_is_deterministic(self):
        durations = {0: 10, 1: 5, 3: 3, 2: 2, 4: 8}
        filled = fill_slack(durations, [[0], [1]])
        reversed_map = dict(reversed(list(durations.items())))
        self.assertEqual(filled, fill_slack(reversed_map, [[0], [1]]))
        self.assertEqual(filled, [[0], [1, 2, 3]])

    def test_no_new_workers_or_unrequested_tests(self):
        durations = {0: 10, 1: 5, 2: 3, 3: 2}
        self.assertEqual(fill_slack(durations, []), [])
        self.assertEqual(fill_slack(durations, [[0], [1]], candidates=[]), [[0], [1]])
        self.assertEqual(fill_slack(durations, [[0], [1]], candidates=[3]), [[0], [1, 3]])

    def test_empty_workers_do_not_count_toward_official_limit(self):
        workers = [[] for _ in range(10)] + [[0]]
        self.assertEqual(fill_slack({0: 1.0}, workers), [[0]])
        self.assertEqual(schedule_cost({0: 1.0}, workers), 3.0)

    def test_tolerance_is_against_fixed_original_makespan(self):
        durations = {0: 1.0, 1: 0.5, 2: 0.501, 3: 0.001}
        filled = fill_slack(durations, [[0], [1]], [2, 3], tolerance=0.001)
        self.assertEqual(filled, [[0, 3], [1, 2]])
        self.assertEqual(schedule_cost(durations, filled), 5.001)
        self.assertEqual(fill_slack(durations, [[0], [1]], [2], tolerance=0.000999), [[0], [1]])

    def test_many_fractional_additions_stay_within_tolerance(self):
        durations = {0: 1.0, 1: 0.1, **{index: 0.0009 for index in range(2, 1002)}}
        filled = fill_slack(durations, [[0], [1]], tolerance=0)
        self.assertLessEqual(max(sum(math.floor(durations[t] * 1000 + 0.5) for t in w) for w in filled), 1000)
        flattened = list(itertools.chain.from_iterable(filled))
        self.assertEqual(len(flattened), len(set(flattened)))

    def test_rejects_invalid_schedules_candidates_and_tolerance(self):
        durations = {0: 3, 1: 2}
        with self.assertRaises(ValueError):
            fill_slack(durations, [[0], [0]])
        with self.assertRaises(KeyError):
            fill_slack(durations, [[0]], [999])
        with self.assertRaises(ValueError):
            schedule_cost(durations, [[0]] * 9)
        for bad in [-1, math.nan, math.inf]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                fill_slack(durations, [[0]], tolerance=bad)


if __name__ == "__main__":
    unittest.main()
