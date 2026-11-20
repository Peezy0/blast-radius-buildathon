"""Deterministic worker assignment for the Blast Radius scoring objective.

``optimize(durations, selected=None, max_workers=8)`` returns a list of nonempty
lists of integer test IDs. ``durations`` maps every known test ID to its recorded
nonnegative duration in seconds. ``selected=None`` schedules every known test;
an explicit empty iterable schedules nothing. Repeated selected IDs are deduped.

The heuristic evaluates longest-processing-time-first (LPT) assignment for each
worker count and minimizes ``max(worker_seconds) + 2 * nonempty_workers``.
Every calculation uses integer milliseconds, matching the official scorer's
``floor(seconds * 1000 + 0.5)`` duration conversion (halves rounded up).
It does not claim an exact solution to the general partitioning problem.
No coverage, sample labels, patch identity, or patch-specific answers are used.
"""

from collections.abc import Iterable, Mapping, Sequence
import math
from numbers import Real


WORKER_OVERHEAD = 2.0
WORKER_OVERHEAD_MILLISECONDS = 2000
MAX_WORKERS = 8


def _durations(values: Mapping[int, Real]) -> dict[int, int]:
    if not isinstance(values, Mapping):
        raise TypeError("durations must be a mapping of test IDs to seconds")
    result = {}
    for test, seconds in values.items():
        _test_id(test)
        if isinstance(seconds, bool) or not isinstance(seconds, Real):
            raise TypeError(f"duration for test {test} must be a real number")
        try:
            seconds = float(seconds)
        except OverflowError as exc:
            raise ValueError(f"duration for test {test} must be finite") from exc
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError(f"duration for test {test} must be finite and nonnegative")
        milliseconds = seconds * 1000
        if not math.isfinite(milliseconds):
            raise ValueError(f"duration for test {test} is too large in milliseconds")
        result[test] = math.floor(milliseconds + 0.5)
    return result


def _test_id(test: int) -> None:
    if isinstance(test, bool) or not isinstance(test, int) or test < 0:
        raise ValueError("test IDs must be nonnegative integers")


def _selected(values: Iterable[int], known: Mapping[int, int]) -> list[int]:
    result = []
    seen = set()
    for test in values:
        _test_id(test)
        if test not in known:
            raise KeyError(f"unknown test ID {test}")
        if test not in seen:
            seen.add(test)
            result.append(test)
    return result


def _workers(workers: Sequence[Sequence[int]], known: Mapping[int, int]):
    result = []
    seen = set()
    for worker in workers:
        group = []
        for test in worker:
            _test_id(test)
            if test not in known:
                raise KeyError(f"unknown test ID {test}")
            if test in seen:
                raise ValueError(f"test ID {test} occurs more than once")
            seen.add(test)
            group.append(test)
        if group:
            result.append(group)
    if len(result) > MAX_WORKERS:
        raise ValueError("at most eight nonempty workers are allowed")
    return result


def optimize(
    durations: Mapping[int, Real],
    selected: Iterable[int] | None = None,
    max_workers: int = MAX_WORKERS,
) -> list[list[int]]:
    """Schedule selected tests using the best of up to eight LPT layouts.

    Results are independent of mapping and selected-input iteration order. Ties
    favor fewer workers, then smaller test/worker IDs. Input is never mutated.
    Invalid durations, unknown selected IDs, and limits outside 1..8 raise.
    """
    if isinstance(max_workers, bool) or not isinstance(max_workers, int):
        raise TypeError("max_workers must be an integer")
    if not 1 <= max_workers <= MAX_WORKERS:
        raise ValueError("max_workers must be between one and eight")
    known = _durations(durations)
    jobs = _selected(known if selected is None else selected, known)
    if not jobs:
        return []
    jobs.sort(key=lambda test: (-known[test], test))
    total = sum(known[test] for test in jobs)
    largest = known[jobs[0]]
    best_cost = None
    best = []

    for count in range(1, min(max_workers, len(jobs)) + 1):
        lower_bound = max(largest, (total + count - 1) // count) + WORKER_OVERHEAD_MILLISECONDS * count
        if best_cost is not None and lower_bound >= best_cost:
            continue
        groups = [[] for _ in range(count)]
        loads = [0] * count
        for test in jobs:
            worker = min(range(count), key=lambda index: (loads[index], index))
            groups[worker].append(test)
            loads[worker] += known[test]
        groups = [group for group in groups if group]
        makespan = max(sum(known[test] for test in group) for group in groups)
        cost = makespan + WORKER_OVERHEAD_MILLISECONDS * len(groups)
        if best_cost is None or cost < best_cost:
            best_cost = cost
            best = groups
    return best


def schedule_cost(
    durations: Mapping[int, Real], workers: Sequence[Sequence[int]]
) -> float:
    """Return the simulated test/worker cost; exclude selector CPU and misses.

    Empty workers are normalized away. Duplicate and unknown test IDs raise.
    """
    known = _durations(durations)
    groups = _workers(workers, known)
    return (
        max((sum(known[t] for t in g) for g in groups), default=0)
        + WORKER_OVERHEAD_MILLISECONDS * len(groups)
    ) / 1000


def fill_slack(
    durations: Mapping[int, Real],
    workers: Sequence[Sequence[int]],
    candidates: Iterable[int] | None = None,
    tolerance: float = 1e-9,
) -> list[list[int]]:
    """Add extra tests without adding workers or increasing the makespan.

    Makespan growth is bounded by ``tolerance`` seconds, rounded down to whole
    milliseconds (the default allows no increase). Explicit candidates are
    considered in their supplied priority order (use an ordered iterable for
    reproducibility); the default considers all remaining tests shortest first,
    breaking ties by ID. A test goes to the tightest worker where it fits.
    Existing assignments are copied, retained, and never duplicated. Empty input
    stays empty because opening a worker would add overhead. This optional step
    uses only test durations, with no patch answers or inferred failure labels.
    """
    if isinstance(tolerance, bool) or not isinstance(tolerance, Real):
        raise TypeError("tolerance must be a real number")
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("tolerance must be finite and nonnegative")
    known = _durations(durations)
    groups = _workers(workers, known)
    if candidates is None:
        candidates = sorted(known, key=lambda test: (known[test], test))
    extras = _selected(candidates, known)
    if not groups:
        return []
    seen = {test for group in groups for test in group}
    loads = [sum(known[test] for test in group) for group in groups]
    if not math.isfinite(tolerance * 1000):
        raise ValueError("tolerance is too large in milliseconds")
    ceiling = max(loads) + math.floor(tolerance * 1000)

    for test in extras:
        if test in seen:
            continue
        feasible = [
            index for index, load in enumerate(loads) if load + known[test] <= ceiling
        ]
        if not feasible:
            continue
        worker = min(feasible, key=lambda index: (ceiling - loads[index] - known[test], index))
        groups[worker].append(test)
        loads[worker] += known[test]
        seen.add(test)

    return groups
