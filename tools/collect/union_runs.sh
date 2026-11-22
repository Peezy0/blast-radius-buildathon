#!/bin/bash
# Union several collection runs (test execution is not fully deterministic:
# e.g. spawned optimizer/executor workers get their own hash seeds, so
# test_execute_tpcds covers ~100-250 different lines run to run).
# Usage: union_runs.sh OUT MAIN_RUN [--cov-only RUN]... [--cmp-only RUN]... [RUN]...
#   MAIN_RUN and RUNs contribute coverage + reads/opens/types; --cov-only RUNs
#   (older plugin versions) contribute line coverage only; --cmp-only RUNs
#   (partial reruns of a few tests) contribute per-test comparison evidence
#   only: their import-time attribution reflects a different import order
#   than the full suite, so their coverage and import lists are not used.
set -eu
OUT=$1; MAIN=$2; shift 2
rm -rf "$OUT"; cp -a "$MAIN" "$OUT"; rm -f "$OUT/map.json.gz"
k=0
while [ $# -gt 0 ]; do
  covonly=0
  if [ "$1" = "--cov-only" ]; then covonly=1; shift; fi
  if [ "$1" = "--cmp-only" ]; then
    R=$2; shift 2; k=$((k+1))
    for f in "$R"/cmp.json "$R"/worker-cmp-*.json; do
      [ -e "$f" ] || continue
      python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); d["import"]=[]; d["files"]=[]; d["obs"]={c:n for c,n in d["obs"].items() if c.endswith("|call") and "::" in c}; json.dump(d,open(sys.argv[2],"w"))' \
        "$f" "$OUT/worker-cmp-run$k-$(basename "$f")"
    done
    continue
  fi
  R=$1; shift; k=$((k+1))
  for f in "$R"/.coverage "$R"/.coverage.*; do
    [ -e "$f" ] || continue
    cp "$f" "$OUT/.coverage.$(basename "$f" | sed 's/^\.coverage\.\{0,1\}//')run$k"
  done
  [ $covonly = 1 ] && continue
  for kind in attrs opens types cmp; do
    [ -e "$R/$kind.json" ] || continue
    cp "$R/$kind.json" "$OUT/worker-$kind-run$k.json"
    for f in "$R"/worker-$kind-*.json; do
      [ -e "$f" ] && cp "$f" "$OUT/worker-$kind-run$k-$(basename "$f" | sed "s/^worker-$kind-//")"
    done
  done
done
echo "union of $((k+1)) runs in $OUT"
