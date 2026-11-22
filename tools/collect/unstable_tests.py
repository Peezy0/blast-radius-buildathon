"""Tests whose line coverage differs between repeated collection runs at the
pinned commit (nondeterministic execution, e.g. spawned workers with their own
hash seeds). Evidence that relies on a single run's per-test observations must
treat these tests conservatively.
Usage (collection image): python unstable_tests.py OUT.json RUN_DIR...
"""
import collections, json, os, shutil, sys, tempfile
import coverage

def per_test(run):
    tmp = tempfile.mkdtemp()
    for f in os.listdir(run):
        if f.startswith(".coverage"):
            shutil.copy(os.path.join(run, f), tmp)
    c = coverage.Coverage(data_file=os.path.join(tmp, ".coverage"))
    c.load()
    c.combine(data_paths=[tmp], keep=True)
    c.save()
    d = coverage.CoverageData(os.path.join(tmp, ".coverage")); d.read()
    out = collections.defaultdict(set)
    for path in d.measured_files():
        if "/sqlglot/sqlglot/" not in path and not path.split("/src/sqlglot/")[-1].startswith("sqlglot/"):
            continue
        for line, ctxs in d.contexts_by_lineno(path).items():
            for ctx in ctxs:
                parts = ctx.split("|")
                if len(parts) >= 2 and parts[-1] == "call" and "::" in parts[-2]:
                    out[parts[-2]].add((path, line))
    shutil.rmtree(tmp)
    return out

runs = [per_test(r) for r in sys.argv[2:]]
tests = set().union(*runs)
unstable, lines = [], {}
for t in sorted(tests):
    seen = [r[t] for r in runs if t in r]  # partial runs only count where t ran
    if len({frozenset(x) for x in seen}) > 1:
        unstable.append(t)
        var = set().union(*seen) - set.intersection(*map(set, seen))
        per = collections.defaultdict(list)
        for path, line in sorted(var):
            per[path.split("/src/sqlglot/")[-1]].append(line)
        lines[t] = per
json.dump({"runs": sys.argv[2:], "unstable": unstable, "lines": lines}, open(sys.argv[1], "w"))
print(len(unstable), "unstable of", len(tests), ";", sum(len(v) for p in lines.values() for v in p.values()), "varying lines")
