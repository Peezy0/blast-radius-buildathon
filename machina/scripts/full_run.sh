#!/usr/bin/env bash
# Streamed full-corpus run for machines with less disk than input+output.
#   pass 1: download each batch, discover identities, delete the batch
#   pass 2: download each batch, write de-identified output, verify, upload, delete
# Needs: python3 with machina/requirements.txt, MACHINA_UPLOAD_TOKEN (pass 2 upload),
# DEST=gs://.../teams/<team>/attempt-N/ . Private state stays in $PRIVATE.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
WORK="${WORK:-$PWD/work}"
PRIVATE="${PRIVATE:-$HOME/.machina-private}"
JOBS="${JOBS:-$(nproc)}"
MAXGB="${MAXGB:-15}"
mkdir -p "$WORK" "$PRIVATE"
run() { PYTHONPATH="$HERE" python3 -m deid "$@"; }

curl -fsS -o "$WORK/manifest.tsv" https://machina-input.joinbuilders.org/manifest.tsv
python3 "$HERE/scripts/plan_batches.py" --manifest "$WORK/manifest.tsv" --out "$WORK/batches" --max-gb "$MAXGB"
run discover --private "$PRIVATE" --manifest "$WORK/manifest.tsv"

if [ "${SKIP_PASS1:-0}" != 1 ]; then
  for b in "$WORK"/batches/*.tsv; do
    python3 "$HERE/scripts/fetch_batch.py" --batch "$b" --dest "$WORK/in"
    run discover --private "$PRIVATE" --input "$WORK/in" --jobs "$JOBS"
    rm -rf "$WORK/in"
  done
fi

for b in "$WORK"/batches/*.tsv; do
  name=$(basename "$b" .tsv)
  [ -f "$PRIVATE/done-$name" ] && continue
  python3 "$HERE/scripts/fetch_batch.py" --batch "$b" --dest "$WORK/in"
  run apply --private "$PRIVATE" --input "$WORK/in" --output "$WORK/corpus" --jobs "$JOBS"
  cp "$PRIVATE/apply-report.json" "$PRIVATE/apply-report-$name.json"
  run verify --private "$PRIVATE" --output "$WORK/corpus"
  cp "$PRIVATE/verify-report.json" "$PRIVATE/verify-report-$name.json"
  if [ -n "${DEST:-}" ]; then
    python3 "$WORK/upload.py" --dest "$DEST" --corpus "$WORK/corpus" ${TOKEN_FILE:+--token-file "$TOKEN_FILE"}
  fi
  rm -rf "$WORK/in" "$WORK/corpus"
  touch "$PRIVATE/done-$name"
done
echo "all batches processed"
