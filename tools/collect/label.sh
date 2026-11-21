#!/bin/bash
# Label mutant patches by running the full pinned suite in the collection image.
# Usage: label.sh SQLGLOT_GIT_CHECKOUT MUT_DIR   (MUT_DIR/commits/*.patch -> MUT_DIR/labels/*.json)
set -u
SRC=$1; MUT=$2; PLUGIN=$(cd "$(dirname "$0")" && pwd)
WORK=$MUT/src; mkdir -p "$MUT/labels"
rm -rf "$WORK"; cp -a "$SRC" "$WORK"
for p in "$MUT"/commits/*.patch; do
  id=$(basename "$p" .patch); out="$MUT/labels/$id.json"
  [ -s "$out" ] || [ -e "$MUT/labels/$id.hang" ] && continue
  git -C "$WORK" checkout -q -- . && git -C "$WORK" clean -qfd -e '!*' >/dev/null
  if ! git -C "$WORK" apply "$p"; then echo "$id apply-failed"; continue; fi
  timeout -k 30 900 docker run --rm --name "br-label-$id" --platform linux/amd64 --network none -v "$WORK":/src/sqlglot -v "$MUT/labels":/labels -v "$PLUGIN":/plugin:ro \
    -e BR_LABEL_OUT=/labels/$id.json -e PYTHONPATH=/src/sqlglot:/plugin -w /src/sqlglot br-collect \
    python -m pytest -p br_label -q -p no:cacheprovider -n 4 --timeout 120 tests > "$MUT/labels/$id.log" 2>&1
  rc=$?; [ $rc -ge 124 ] && [ ! -s "$out" ] && echo "rc=$rc" > "$MUT/labels/$id.hang"  # hung past the 900 s limit: excluded
  docker rm -f "br-label-$id" >/dev/null 2>&1
  echo "$id $(tail -1 "$MUT/labels/$id.log")"
done
git -C "$WORK" checkout -q -- .
