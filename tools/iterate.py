#!/usr/bin/env python3
"""Evaluate a finite candidate queue and retain an evidence-gated local champion.

This tool neither generates code nor publishes or submits anything. Every
candidate runs in fresh source snapshots, with regression tests copied from the
trusted project. Local subprocesses have ordinary local OS permissions: this is
an evidence gate, not a security sandbox or cloud execution service.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import statistics
import subprocess
import sys
import tempfile
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DEADLINE = "2026-11-22T16:00:00+00:00"
TRUSTED_TEST_NAMES = ("test_impact.py", "test_scheduling.py", "test_selector.py")
MAX_TEST_OUTPUT = 1_048_576


class IterationError(ValueError):
    """A bounded, expected iteration setup or evidence error."""


def _json_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest(value) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _file_sha(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise IterationError(f"expected a regular non-symlink file: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_manifest(directory: Path | str) -> dict[str, str]:
    """Hash selector.py, optional Dockerfile, and regular src files; no symlinks."""
    directory = Path(directory).absolute()
    if directory.is_symlink() or not directory.is_dir():
        raise IterationError(f"candidate must be a regular directory: {directory}")
    files = [directory / "selector.py"]
    if (directory / "Dockerfile").exists() or (directory / "Dockerfile").is_symlink():
        files.append(directory / "Dockerfile")
    source = directory / "src"
    if source.is_symlink():
        raise IterationError(f"source directory cannot be a symlink: {source}")
    if source.exists():
        for path in source.rglob("*"):
            if path.is_symlink():
                raise IterationError(f"source symlinks are unsupported: {path}")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc":
                files.append(path)
    return {str(path.relative_to(directory)): _file_sha(path) for path in sorted(files)}


def _copy_source(source: Path, destination: Path, manifest: dict[str, str]) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for relative in manifest:
        path = source / relative
        if _file_sha(path) != manifest[relative]:
            raise IterationError("source changed before its snapshot could be copied")
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    if source_manifest(destination) != manifest:
        raise IterationError("source snapshot does not match the expected manifest")


def _data_manifest(data_dir: Path, answers: Path, scorer: Path) -> dict:
    if data_dir.is_symlink() or not data_dir.is_dir():
        raise IterationError("data directory must not be a symlink")
    inputs = [data_dir / "tests.json"]
    commits = data_dir / "commits"
    if commits.is_symlink() or not commits.is_dir():
        raise IterationError("data must have a non-symlink commits directory")
    for path in commits.rglob("*"):
        if path.is_symlink():
            raise IterationError("sample input symlinks are unsupported")
        if path.is_file() and path.suffix == ".patch":
            inputs.append(path)
    return {
        "inputs": {str(p.relative_to(data_dir)): _file_sha(p) for p in sorted(inputs)},
        "answers_sha256": _file_sha(answers),
        "scorer_sha256": _file_sha(scorer),
    }


def _trusted_test_manifest(trusted_root: Path) -> dict[str, str]:
    return {
        "tests/" + name: _file_sha(trusted_root / "tests" / name)
        for name in TRUSTED_TEST_NAMES
    }


def _atomic_json(path: Path, value) -> None:
    descriptor, name = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(value) + b"\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def _run_lock(output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    if output_dir.is_symlink():
        raise IterationError("output directory cannot be a symlink")
    path = output_dir / ".iteration.lock"
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise IterationError(f"iteration run is already locked: {output_dir}") from exc
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(str(os.getpid()) + "\n")
        if (output_dir / "evidence.jsonl").exists() or (output_dir / "champion.json").exists():
            raise IterationError("output directory already contains a run; choose a new run directory")
        yield
    finally:
        path.unlink(missing_ok=True)


def _append_evidence(output_dir: Path, value) -> None:
    with (output_dir / "evidence.jsonl").open("ab") as stream:
        stream.write(_json_bytes(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def _parse_deadline(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IterationError("deadline must be an ISO timestamp with timezone") from exc
    if result.tzinfo is None:
        raise IterationError("deadline must include a timezone")
    return result.astimezone(timezone.utc)


def _remaining(deadline: datetime, clock: Callable[[], datetime]) -> float:
    return max(0.0, (deadline - clock().astimezone(timezone.utc)).total_seconds())


def trusted_test_gate(snapshot: Path, trusted_root: Path, timeout: float) -> dict:
    """Test copied candidate code with a fixed, trusted selector test suite."""
    before = _trusted_test_manifest(trusted_root)
    tests_dir = snapshot / "tests"
    tests_dir.mkdir()
    for name in TRUSTED_TEST_NAMES:
        shutil.copyfile(trusted_root / "tests" / name, tests_dir / name)
    # These trusted tests validate the public sample using the official scorer.
    official = trusted_root / "official"
    if official.exists():
        if any(path.is_symlink() for path in official.rglob("*")):
            raise IterationError("trusted official fixtures cannot contain symlinks")
        shutil.copytree(official, snapshot / "official", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    copied_tests = _trusted_test_manifest(snapshot)
    if copied_tests != before:
        raise IterationError("trusted tests changed while copying")
    env = dict(os.environ)
    env.pop("PYTHONHOME", None)
    env["PYTHONPATH"] = str(snapshot)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    started = datetime.now(timezone.utc)
    timed_out = False
    with tempfile.TemporaryFile() as output:
        process = subprocess.Popen(
            [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"],
            cwd=snapshot, env=env, stdout=output, stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
        output.seek(0, os.SEEK_END)
        output_bytes = output.tell()
        output.seek(max(0, output_bytes - 64_000))
        tail = output.read().decode("utf-8", errors="replace")
    tests_match = re.search(r"Ran (\d+) tests? in ", tail)
    tests_count = int(tests_match.group(1)) if tests_match else 0
    unchanged = _trusted_test_manifest(trusted_root) == before and _trusted_test_manifest(snapshot) == before
    reasons = []
    if timed_out:
        reasons.append("trusted tests timed out")
    if process.returncode != 0:
        reasons.append("trusted tests failed")
    if not tests_count:
        reasons.append("trusted tests did not report executing any tests")
    if output_bytes > MAX_TEST_OUTPUT:
        reasons.append("trusted test output exceeded the limit")
    if not unchanged:
        reasons.append("trusted test files changed during the gate")
    return {
        "ok": not reasons, "reasons": reasons, "returncode": process.returncode,
        "timed_out": timed_out, "tests_run": tests_count, "trusted_tests_unchanged": unchanged,
        "trusted_tests_sha256": _digest(before), "output_bytes": output_bytes,
        "output_tail": tail, "started_at": started.isoformat(),
    }


def _evaluation_problems(result: dict, repeats: int) -> list[str]:
    problems = []
    if not result.get("ok") or not result.get("verified"):
        problems.append("runtime evaluation was not verified")
    if result.get("verification_scope") != "local_subprocess":
        problems.append("runtime evaluation has an unexpected verification scope")
    if not result.get("deterministic"):
        problems.append("runtime output was not deterministic")
    runs = result.get("runs", [])
    if len(runs) != repeats:
        problems.append("runtime did not complete every requested repeat")
    hashes = set()
    for run in runs:
        validation = run.get("output_validation", {})
        if not validation.get("ok") or not isinstance(validation.get("missed_commits"), list):
            problems.append("runtime output lacks valid per-commit miss evidence")
        if run.get("returncode") != 0 or run.get("error") or not run.get("source_unchanged"):
            problems.append("runtime failed, timed out, or changed its source")
        if not isinstance(run.get("cpu_microseconds"), int) or run.get("cpu_microseconds", -1) < 0:
            problems.append("runtime CPU evidence is unavailable")
        if not isinstance(run.get("total_microseconds"), int) or run.get("total_microseconds", -1) < 0:
            problems.append("runtime total evidence is unavailable")
        if run.get("stdout_sha256"):
            hashes.add(run["stdout_sha256"])
        else:
            problems.append("runtime stdout digest is missing")
    if len(hashes) != 1:
        problems.append("runtime stdout differs across repeats")
    if not isinstance(result.get("median_total_microseconds"), int):
        problems.append("runtime median score is unavailable")
    elif runs and all(isinstance(run.get("total_microseconds"), int) for run in runs):
        if result["median_total_microseconds"] != round(statistics.median(run["total_microseconds"] for run in runs)):
            problems.append("runtime median does not match per-run score evidence")
    return sorted(set(problems))


def _promotion_problem(champion: dict, candidate: dict, margin_microseconds: int) -> str | None:
    previous_misses = set(champion["runs"][0]["output_validation"]["missed_commits"])
    for run in candidate["runs"]:
        new_misses = set(run["output_validation"]["missed_commits"])
        if not new_misses.issubset(previous_misses):
            return "candidate introduced a newly missed commit"
    if candidate.get("misses", 0) > champion.get("misses", 0):
        return "candidate increased the missed-commit count"
    spreads = [max(r["total_microseconds"] for r in e["runs"]) - min(r["total_microseconds"] for r in e["runs"]) for e in (champion, candidate)]
    required_margin = max(margin_microseconds, *spreads)
    improvement = champion["median_total_microseconds"] - candidate["median_total_microseconds"]
    if improvement <= required_margin:
        return f"improvement {improvement} us did not exceed required margin {required_margin} us"
    return None


def run_iteration(
    baseline, candidates, output_dir, data_dir, answers_path, scorer_path,
    repeats=3, timeout=180, deadline=DEFAULT_DEADLINE, margin_seconds=1.0,
    *, trusted_root=ROOT, evaluator=None, clock=None,
) -> dict:
    """Process a finite queue once. Only verified improvements become champions.

    ``evaluator``, ``trusted_root`` and ``clock`` are dependency-injection hooks
    for harness tests. The CLI always uses the trusted project and evaluator.
    """
    if not isinstance(repeats, int) or isinstance(repeats, bool) or repeats < 3:
        raise IterationError("at least three evaluation repeats are required")
    if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
        raise IterationError("timeout must be finite and positive")
    if not isinstance(margin_seconds, (int, float)) or not math.isfinite(margin_seconds) or margin_seconds < 0:
        raise IterationError("margin must be finite and nonnegative")
    deadline_value = _parse_deadline(deadline)
    clock = clock or (lambda: datetime.now(timezone.utc))
    baseline = Path(baseline).absolute()
    candidates = [Path(path).absolute() for path in candidates]
    output_dir = Path(output_dir).absolute()
    data_dir, answers_path, scorer_path = map(lambda p: Path(p).absolute(), (data_dir, answers_path, scorer_path))
    trusted_root = Path(trusted_root).absolute()
    with _run_lock(output_dir):
        summary = {"status": "running", "verification_scope": "local_subprocess", "deadline": deadline_value.isoformat(), "champion": None, "evaluated": 0, "promoted": 0, "rejected": 0, "duplicates": 0}
        if _remaining(deadline_value, clock) <= 0:
            summary["status"] = "deadline_reached"
            _append_evidence(output_dir, {"event": "stopped", "reason": "deadline already reached"})
            _atomic_json(output_dir / "summary.json", summary)
            return summary
        trusted_tests = _trusted_test_manifest(trusted_root)
        inputs = _data_manifest(data_dir, answers_path, scorer_path)
        input_sha = _digest({"data": inputs, "trusted_tests": trusted_tests})
        _append_evidence(output_dir, {"event": "run_started", "deadline": deadline_value.isoformat(), "input_sha256": input_sha, "inputs": inputs, "trusted_tests": trusted_tests})
        if evaluator is None:
            if str(ROOT) not in sys.path:
                sys.path.insert(0, str(ROOT))
            from tools.evaluate_candidate import evaluate
            evaluator = evaluate
        seen = set()
        champion_evaluation = None

        for index, candidate in enumerate([baseline, *candidates]):
            is_baseline = index == 0
            if _remaining(deadline_value, clock) <= 0:
                summary["status"] = "deadline_reached"
                break
            evidence = {"event": "baseline" if is_baseline else "candidate", "candidate_dir": str(candidate), "input_sha256": input_sha, "accepted": False, "reasons": []}
            try:
                manifest = source_manifest(candidate)
                sha = _digest(manifest)
                evidence.update({"source_manifest": manifest, "source_sha256": sha})
                cache_key = _digest({"source": sha, "inputs": input_sha})
                if cache_key in seen:
                    evidence["reasons"].append("duplicate source and input fingerprint in this run")
                    summary["duplicates"] += 1
                    _append_evidence(output_dir, evidence)
                    continue
                seen.add(cache_key)
                with tempfile.TemporaryDirectory(prefix="blast-iterate-") as temporary:
                    temporary = Path(temporary)
                    gate_snapshot = temporary / "unit"
                    runtime_snapshot = temporary / "runtime"
                    _copy_source(candidate, gate_snapshot, manifest)
                    gate_budget = min(timeout, _remaining(deadline_value, clock))
                    if gate_budget <= 0:
                        raise IterationError("deadline reached before trusted tests")
                    gate = trusted_test_gate(gate_snapshot, trusted_root, gate_budget)
                    evidence["unit_tests"] = gate
                    if not gate["ok"]:
                        raise IterationError("trusted unit test gate failed")
                    if source_manifest(gate_snapshot) != manifest or source_manifest(candidate) != manifest:
                        raise IterationError("candidate source changed during trusted tests")
                    if _trusted_test_manifest(trusted_root) != trusted_tests or _data_manifest(data_dir, answers_path, scorer_path) != inputs:
                        raise IterationError("trusted tests or scoring inputs changed during tests")
                    # The runtime snapshot has source only: no answers, official
                    # fixture directory, or unit test files are passed to it.
                    _copy_source(candidate, runtime_snapshot, manifest)
                    runtime_budget = min(timeout, _remaining(deadline_value, clock) / repeats)
                    if runtime_budget <= 0:
                        raise IterationError("deadline reached before runtime evaluation")
                    result = evaluator(runtime_snapshot, data_dir, answers_path, scorer_path, repeats=repeats, timeout=runtime_budget)
                    evidence["evaluation"] = result
                    summary["evaluated"] += 1
                    evidence["reasons"].extend(_evaluation_problems(result, repeats))
                    if source_manifest(runtime_snapshot) != manifest or source_manifest(candidate) != manifest:
                        evidence["reasons"].append("candidate source changed during runtime evaluation")
                    if _trusted_test_manifest(trusted_root) != trusted_tests or _data_manifest(data_dir, answers_path, scorer_path) != inputs:
                        evidence["reasons"].append("trusted tests or scoring inputs changed during evaluation")
                    if _remaining(deadline_value, clock) <= 0:
                        evidence["reasons"].append("deadline reached before champion promotion")
                    if not evidence["reasons"] and champion_evaluation is not None:
                        problem = _promotion_problem(champion_evaluation, result, round(margin_seconds * 1_000_000))
                        if problem:
                            evidence["reasons"].append(problem)
                    if not evidence["reasons"]:
                        retained = output_dir / "snapshots" / sha
                        _copy_source(runtime_snapshot, retained, manifest)
                        evidence["accepted"] = True
                        champion_evaluation = result
                        summary["champion"] = {"status": "provisional_local_champion", "candidate_dir": str(candidate), "retained_source_dir": str(retained), "source_manifest": manifest, "source_sha256": sha, "input_sha256": input_sha, "median_total_microseconds": result["median_total_microseconds"], "misses": result.get("misses"), "verification_scope": "local_subprocess", "container_verified": False, "cloud_execution_verified": False}
                        if not is_baseline:
                            summary["promoted"] += 1
                        _append_evidence(output_dir, evidence)
                        _atomic_json(output_dir / "champion.json", summary["champion"])
            except (IterationError, OSError, ValueError, KeyError, TypeError) as error:
                evidence["reasons"].append(str(error))
            if not evidence["accepted"]:
                summary["rejected"] += 1
                _append_evidence(output_dir, evidence)
                if is_baseline:
                    summary["status"] = "deadline_reached" if _remaining(deadline_value, clock) <= 0 else "baseline_failed"
                    break
                if _trusted_test_manifest(trusted_root) != trusted_tests or _data_manifest(data_dir, answers_path, scorer_path) != inputs:
                    summary["status"] = "trusted_inputs_changed"
                    break
            if _remaining(deadline_value, clock) <= 0:
                summary["status"] = "deadline_reached"
                break
        if summary["status"] == "running":
            summary["status"] = "completed"
        _atomic_json(output_dir / "summary.json", summary)
        _append_evidence(output_dir, {"event": "run_finished", "status": summary["status"], "champion_sha256": summary["champion"]["source_sha256"] if summary["champion"] else None})
        return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--candidate", action="append", default=[])
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--data-dir", default=str(ROOT / "official/sample"))
    parser.add_argument("--answers", default=str(ROOT / "official/sample/answers.json"))
    parser.add_argument("--scorer", default=str(ROOT / "official/score.py"))
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--deadline", default=DEFAULT_DEADLINE)
    parser.add_argument("--margin-seconds", type=float, default=1.0)
    args = parser.parse_args(argv)
    try:
        result = run_iteration(args.baseline, args.candidate, args.output_dir, args.data_dir, args.answers, args.scorer, args.repeats, args.timeout, args.deadline, args.margin_seconds)
    except (IterationError, OSError, ValueError) as error:
        print(json.dumps({"status": "setup_failed", "reason": str(error)}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
