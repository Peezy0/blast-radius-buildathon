"""Blast Radius runtime: deterministic, conservative test selection and scheduling.

The default selects every catalog test, so its savings come solely from worker
packing. An explicitly supplied baseline coverage map enables optional selection.
The runtime never reads sample answers, sample IDs, or development data.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from src.impact import choose_tests
from src.scheduling import optimize


# Use a conservative decimal interpretation of the advertised 2 MB limit.
MAX_OUTPUT_BYTES = 2_000_000


def _reject_constant(value: str):
    raise ValueError(f"nonstandard JSON constant: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def load_json(path: Path):
    return json.loads(
        path.read_text(encoding="utf-8"),
        parse_constant=_reject_constant,
        object_pairs_hook=_unique_object,
    )


def load_catalog(path: Path) -> dict[int, float]:
    catalog = load_json(path)
    if not isinstance(catalog, dict) or not isinstance(catalog.get("tests"), list):
        raise ValueError("catalog must be an object containing a tests list")
    durations = {}
    identities = set()
    for index, test in enumerate(catalog["tests"]):
        if not isinstance(test, dict):
            raise ValueError(f"catalog test {index} must be an object")
        if not {"number", "id", "file", "seconds"} <= test.keys():
            raise ValueError(f"catalog test {index} is missing a required field")
        number = test["number"]
        if type(number) is not int or number != index:
            raise ValueError("catalog test numbers must start at zero with no gaps")
        if not isinstance(test["id"], str) or not test["id"]:
            raise ValueError(f"catalog test {index} has an invalid id")
        if test["id"] in identities:
            raise ValueError(f"catalog test {index} repeats an id")
        if not isinstance(test["file"], str) or not test["file"]:
            raise ValueError(f"catalog test {index} has an invalid file")
        seconds = test["seconds"]
        if (
            isinstance(seconds, bool)
            or not isinstance(seconds, (int, float))
            or not math.isfinite(seconds)
            or seconds < 0
        ):
            raise ValueError(f"catalog test {index} has an invalid duration")
        durations[number] = seconds
        identities.add(test["id"])
    return durations


def generate_output(data_dir: Path, coverage=None) -> tuple[bytes, dict]:
    """Validate inputs and prepare the entire bounded output before emitting it."""
    durations = load_catalog(data_dir / "tests.json")
    commit_dir = data_dir / "commits"
    if not commit_dir.is_dir():
        raise ValueError("data directory must contain a commits directory")
    all_tests = frozenset(durations)
    cache = {}
    chunks = []
    size = 0
    commit_count = 0
    narrowed = 0
    for patch in sorted(commit_dir.glob("*.patch")):
        if not patch.is_file():
            raise ValueError(f"patch is not a regular file: {patch.name}")
        # Default mode need not parse or read a patch to select the full suite.
        selected = all_tests
        if coverage is not None:
            selected = frozenset(
                choose_tests(patch.read_text(encoding="utf-8"), all_tests, coverage)
            )
        if not selected <= all_tests:
            raise ValueError("coverage selected a test outside the current catalog")
        if selected not in cache:
            cache[selected] = optimize(durations, selected)
        workers = cache[selected]
        line = json.dumps(
            {"commit": patch.stem, "workers": workers},
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8") + b"\n"
        size += len(line)
        if size > MAX_OUTPUT_BYTES:
            raise ValueError(f"output exceeds {MAX_OUTPUT_BYTES} bytes")
        chunks.append(line)
        commit_count += 1
        narrowed += selected != all_tests
    return b"".join(chunks), {
        "commits": commit_count,
        "tests": len(durations),
        "schedules": len(cache),
        "narrowed": narrowed,
        "output_bytes": size,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=Path("/data"))
    parser.add_argument("--coverage", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        coverage = load_json(args.coverage) if args.coverage else None
        output, stats = generate_output(args.data_dir, coverage)
    except (OSError, ValueError, TypeError, KeyError, OverflowError) as error:
        print(f"selector: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(output)
    print(json.dumps(stats, sort_keys=True), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
