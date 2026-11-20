"""Repeated local verification of a checkpoint using the trusted official scorer.

This evaluator does not provide a filesystem/network security sandbox. It never
passes answers to the candidate, but a local process can access other host files.
Source hashes before/after execution detect persistent checkpoint modifications;
they do not establish that an adversarial process never temporarily edited files.
Local subprocess CPU is evidence for this machine, not scorer-container CPU.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import selectors
import signal
import statistics
import subprocess
import sys
import tempfile
import time


ROOT = Path(__file__).resolve().parents[1]
TRUSTED_SCORER = ROOT / "official" / "score.py"
MAX_OUTPUT_BYTES = 2_000_000
MAX_STDERR_BYTES = 65_536


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_sha256(manifest: dict[str, str]) -> str:
    return _sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode())


def source_manifest(candidate_dir: Path | str) -> dict[str, str]:
    """Hash the immutable runtime checkpoint, rejecting source symlinks."""
    root = Path(candidate_dir).resolve(strict=True)
    if not (root / "selector.py").is_file() or not (root / "src").is_dir():
        raise ValueError("candidate must contain selector.py and a src directory")
    if (root / "src").is_symlink():
        raise ValueError("source symlinks are unsupported")
    files = [root / "selector.py", *sorted((root / "src").rglob("*.py"))]
    if (root / "Dockerfile").exists():
        files.append(root / "Dockerfile")
    result = {}
    for path in files:
        if path.is_symlink() or any(
            parent.is_symlink() for parent in path.parents if parent != root
            and root in parent.parents
        ):
            raise ValueError("source symlinks are unsupported")
        if not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("source must be a regular file inside the candidate")
        result[path.relative_to(root).as_posix()] = _sha256(path.read_bytes())
    return result


def _input_manifest(data: Path, answers: Path, scorer: Path) -> dict[str, str]:
    result = {"tests.json": _sha256((data / "tests.json").read_bytes())}
    for patch in sorted((data / "commits").glob("*.patch")):
        result["commits/" + patch.name] = _sha256(patch.read_bytes())
    result["trusted_answers"] = _sha256(answers.read_bytes())
    result["trusted_scorer"] = _sha256(scorer.read_bytes())
    return result


def _kill_group(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except ProcessLookupError:
        pass


def _execute(candidate: Path, data: Path, timeout: float, temporary: Path) -> dict:
    """Bound output and measure the specific child using wait4 where available."""
    env = {
        "PATH": os.defpath,
        "HOME": str(temporary),
        "TMPDIR": str(temporary),
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0",
        "PYTHONIOENCODING": "utf-8",
    }
    if "SystemRoot" in os.environ:
        env["SystemRoot"] = os.environ["SystemRoot"]
    started = time.monotonic()
    process = subprocess.Popen(
        [sys.executable, str(candidate / "selector.py"), "--data-dir", str(data)],
        cwd=candidate,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=False,
        start_new_session=(os.name == "posix"),
    )
    stdout = bytearray()
    stderr = bytearray()
    output_count = 0
    error = None
    usage = None
    reaped = False
    has_wait4 = hasattr(os, "wait4")
    selector = selectors.DefaultSelector()
    try:
        for stream, name in [(process.stdout, "stdout"), (process.stderr, "stderr")]:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ, name)
        while selector.get_map() or not reaped:
            remaining = timeout - (time.monotonic() - started)
            if remaining <= 0:
                error = "timeout"
                break
            for key, _ in selector.select(min(0.02, remaining)):
                chunk = os.read(key.fileobj.fileno(), 65_536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    key.fileobj.close()
                    continue
                if key.data == "stdout":
                    output_count += len(chunk)
                    if output_count > MAX_OUTPUT_BYTES:
                        error = "stdout_limit"
                        break
                    stdout.extend(chunk)
                else:
                    stderr.extend(chunk)
                    if len(stderr) > MAX_STDERR_BYTES:
                        del stderr[:-MAX_STDERR_BYTES]
            if error:
                break
            if not reaped:
                if has_wait4:
                    pid, status, child_usage = os.wait4(process.pid, os.WNOHANG)
                    if pid:
                        usage = child_usage
                        process.returncode = os.waitstatus_to_exitcode(status)
                        reaped = True
                elif process.poll() is not None:
                    reaped = True
        if error:
            _kill_group(process)
        if not reaped:
            if has_wait4:
                _, status, usage = os.wait4(process.pid, 0)
                process.returncode = os.waitstatus_to_exitcode(status)
            else:
                process.wait()
            reaped = True
    finally:
        if not reaped:
            _kill_group(process)
            if has_wait4:
                try:
                    _, status, usage = os.wait4(process.pid, 0)
                    process.returncode = os.waitstatus_to_exitcode(status)
                except ChildProcessError:
                    pass
            else:
                process.wait()
        # A completed selector must not leave same-session background processes.
        # This is cleanup, not a security sandbox: a hostile process can escape
        # its process group and source checks cannot prevent that.
        _kill_group(process)
        for key in list(selector.get_map().values()):
            key.fileobj.close()
        selector.close()
    cpu_microseconds = (
        round((usage.ru_utime + usage.ru_stime) * 1_000_000) if usage else None
    )
    if error is None and process.returncode != 0:
        error = "nonzero_exit"
    return {
        "returncode": process.returncode,
        "wall_seconds": time.monotonic() - started,
        "cpu_microseconds": cpu_microseconds,
        "cpu_measurement": "wait4" if usage else "unavailable",
        "stdout_sha256": _sha256(bytes(stdout)),
        "stdout_bytes": output_count,
        "stderr_tail": bytes(stderr).decode("utf-8", errors="replace"),
        "error": error,
        "_stdout": bytes(stdout),
    }


def evaluate(
    candidate_dir: Path | str,
    data_dir: Path | str,
    answers_path: Path | str,
    scorer_path: Path | str,
    repeats: int = 3,
    timeout: float = 180,
) -> dict:
    """Return JSON-ready local evidence; expected validation failures set ok=False.

    At least three runs are required. The caller must supply the official scorer
    from this trusted checkpoint, outside the candidate directory. Each repeat
    gets a new temporary output directory and a minimal environment.
    """
    result = {
        "ok": False,
        "verified": False,
        "verification_scope": "local_subprocess",
        "isolation": "none; local subprocess can access host files",
        "cpu_scope": "wait4 child user+system CPU; includes descendants the child reaps",
        "reasons": [],
        "deterministic": False,
        "source_manifest": {},
        "source_sha256": None,
        "input_manifest": {},
        "runs": [],
        "median_total_microseconds": None,
        "simulated_milliseconds": None,
        "misses": None,
        "cpu_measurement": "unavailable",
    }
    try:
        if type(repeats) is not int or repeats < 3:
            raise ValueError("repeats must be an integer of at least three")
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not 0 < timeout < float("inf"):
            raise ValueError("timeout must be positive and finite")
        candidate = Path(candidate_dir).resolve(strict=True)
        data = Path(data_dir).resolve(strict=True)
        answers_file = Path(answers_path).resolve(strict=True)
        scorer_file = Path(scorer_path).resolve(strict=True)
        if scorer_file != TRUSTED_SCORER.resolve(strict=True):
            raise ValueError("scorer must be the trusted checkpoint official/score.py")
        if scorer_file.is_relative_to(candidate):
            raise ValueError("trusted scorer must be outside the candidate")
        if not (data / "commits").is_dir():
            raise ValueError("dataset must contain commits directory")
        initial_source = source_manifest(candidate)
        result["source_manifest"] = initial_source
        result["source_sha256"] = manifest_sha256(initial_source)
        inputs = _input_manifest(data, answers_file, scorer_file)
        result["input_manifest"] = inputs
        tests = json.loads((data / "tests.json").read_text("utf-8"))["tests"]
        answers = json.loads(answers_file.read_text("utf-8"))["commits"]
        if set(answers) != {path.stem for path in (data / "commits").glob("*.patch")}:
            raise ValueError("answer commits do not match dataset patch files")
        spec = importlib.util.spec_from_file_location("_trusted_blast_radius_scorer", scorer_file)
        scorer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(scorer)
        for index in range(repeats):
            if source_manifest(candidate) != initial_source:
                result["reasons"].append("source_mutation_before_run")
                break
            with tempfile.TemporaryDirectory(prefix="blast-radius-evaluate-") as temp:
                run = _execute(candidate, data, timeout, Path(temp))
            run["repeat"] = index + 1
            stdout = run.pop("_stdout")
            try:
                run["source_unchanged"] = source_manifest(candidate) == initial_source
            except (OSError, ValueError):
                run["source_unchanged"] = False
            try:
                run["inputs_unchanged"] = _input_manifest(data, answers_file, scorer_file) == inputs
            except OSError:
                run["inputs_unchanged"] = False
            run["output_validation"] = scorer.evaluate_run(stdout, tests, answers)
            run["total_microseconds"] = None
            if run["output_validation"]["ok"] and run["cpu_microseconds"] is not None:
                run["total_microseconds"] = (
                    run["output_validation"]["simulated_milliseconds"] * 1000
                    + run["cpu_microseconds"]
                )
            result["runs"].append(run)
            if run["error"]:
                result["reasons"].append(run["error"])
            if not run["source_unchanged"]:
                result["reasons"].append("source_mutation")
            if not run["inputs_unchanged"]:
                result["reasons"].append("trusted_input_mutation")
            if not run["output_validation"]["ok"]:
                result["reasons"].append("invalid_output:" + run["output_validation"]["code"])
            if result["reasons"]:
                break
        runs = result["runs"]
        result["deterministic"] = len(runs) == repeats and len({run["stdout_sha256"] for run in runs}) == 1
        if len(runs) == repeats and not result["deterministic"]:
            result["reasons"].append("nondeterministic_output")
        if not result["reasons"] and len(runs) == repeats:
            result["ok"] = result["verified"] = True
            result["simulated_milliseconds"] = runs[0]["output_validation"]["simulated_milliseconds"]
            result["misses"] = len(runs[0]["output_validation"]["missed_commits"])
            if all(run["cpu_measurement"] == "wait4" for run in runs):
                result["cpu_measurement"] = "wait4"
            totals = [run["total_microseconds"] for run in runs]
            if all(total is not None for total in totals):
                result["median_total_microseconds"] = round(statistics.median(totals))
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as error:
        result["reasons"].append(f"evaluation_error:{type(error).__name__}:{error}")
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--scorer", type=Path, default=TRUSTED_SCORER)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args(argv)
    result = evaluate(args.candidate, args.data_dir, args.answers, args.scorer, args.repeats, args.timeout)
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
