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

# Optional durable copy of the private dir (key, mapping, progress markers, reports).
backup() { if [ -n "${BACKUP:-}" ]; then mkdir -p "$BACKUP" && cp -a "$PRIVATE"/. "$BACKUP"/; fi; }
if [ -n "${BACKUP:-}" ] && [ -f "$BACKUP/secret.key" ] && [ ! -f "$PRIVATE/secret.key" ]; then
  cp -a "$BACKUP"/. "$PRIVATE"/   # resume after the container was replaced
fi

if [ "${SKIP_PASS1:-0}" != 1 ] && [ "${SINGLE_PASS:-0}" != 1 ]; then
  for b in "$WORK"/batches/*.tsv; do
    name=$(basename "$b" .tsv)
    [ -f "$PRIVATE/discovered-$name" ] && continue
    python3 "$HERE/scripts/fetch_batch.py" --batch "$b" --dest "$WORK/in" || true
    run discover --private "$PRIVATE" --input "$WORK/in" --jobs "$JOBS"
    rm -rf "$WORK/in"
    touch "$PRIVATE/discovered-$name"
    backup
  done
fi
[ "${PASS1_ONLY:-0}" = 1 ] && { echo "pass 1 complete"; exit 0; }

for b in "$WORK"/batches/*.tsv; do
  name=$(basename "$b" .tsv)
  [ -f "$PRIVATE/done-$name" ] && continue
  python3 "$HERE/scripts/fetch_batch.py" --batch "$b" --dest "$WORK/in"
  if [ "${SINGLE_PASS:-0}" = 1 ]; then
    # Fast mode: discover this batch, then apply. Pseudonyms are deterministic, so a
    # name first found in a later batch is never inconsistent, only missed earlier.
    run discover --private "$PRIVATE" --input "$WORK/in" --jobs "$JOBS"
    backup
  fi
  if [ -z "${DEST:-}" ] && [ -d "$WORK/corpus" ] && [ "${KEEP_LOCAL:-0}" != 1 ]; then
    echo "no DEST: stopping before $name so finished output is not deleted un-uploaded"; exit 3
  fi
  run apply --private "$PRIVATE" --input "$WORK/in" --output "$WORK/corpus" --jobs "$JOBS"
  cp "$PRIVATE/apply-report.json" "$PRIVATE/apply-report-$name.json"
  run verify --private "$PRIVATE" --output "$WORK/corpus"
  cp "$PRIVATE/verify-report.json" "$PRIVATE/verify-report-$name.json"
  if [ -n "${DEST:-}" ]; then
    # Token comes from a file (never argv, chat or git); upload.py re-reads it when it expires.
    [ -n "${TOKEN_FILE:-}" ] || { echo "set TOKEN_FILE to a 0600 file holding the upload token" >&2; exit 2; }
    MACHINA_UPLOAD_TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")" \
      python3 "$WORK/upload.py" --dest "$DEST" --corpus "$WORK/corpus" --token-file "$TOKEN_FILE"
  fi
  rm -rf "$WORK/in"
  if [ -n "${DEST:-}" ]; then rm -rf "$WORK/corpus"; fi   # keep output until it is uploaded
  touch "$PRIVATE/done-$name"
  backup
done
echo "all batches processed"
