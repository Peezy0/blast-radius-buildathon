#!/bin/bash
# Union several collection runs (test execution is not fully deterministic:
# e.g. spawned optimizer/executor workers get their own hash seeds, so
# test_execute_tpcds covers ~100-250 different lines run to run).
# Usage: union_runs.sh OUT MAIN_RUN [--cov-only RUN]... [RUN]...
#   MAIN_RUN and RUNs contribute coverage + reads/opens/types; --cov-only RUNs
#   (older plugin versions) contribute line coverage only.
set -eu
OUT=$1; MAIN=$2; shift 2
rm -rf "$OUT"; cp -a "$MAIN" "$OUT"; rm -f "$OUT/map.json.gz"
k=0
while [ $# -gt 0 ]; do
  covonly=0
  if [ "$1" = "--cov-only" ]; then covonly=1; shift; fi
  R=$1; shift; k=$((k+1))
  for f in "$R"/.coverage "$R"/.coverage.*; do
    [ -e "$f" ] || continue
    cp "$f" "$OUT/.coverage.$(basename "$f" | sed 's/^\.coverage\.\{0,1\}//')run$k"
  done
  [ $covonly = 1 ] && continue
  for kind in attrs opens types; do
    cp "$R/$kind.json" "$OUT/worker-$kind-run$k.json"
    for f in "$R"/worker-$kind-*.json; do
      [ -e "$f" ] && cp "$f" "$OUT/worker-$kind-run$k-$(basename "$f" | sed "s/^worker-$kind-//")"
    done
  done
done
echo "union of $((k+1)) runs in $OUT"
