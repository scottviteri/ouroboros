#!/usr/bin/env bash
# Create a new lineage from the canonical organism owned by this instrument.
set -euo pipefail

INSTRUMENT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LINEAGE="${LINEAGE:?path for the new lineage worktree}"
GITDIR="${GITDIR:-$LINEAGE.git}"
SEED="$INSTRUMENT_DIR/organism.el"
RUNTIME="$INSTRUMENT_DIR/runtime.py"
RUNNER="$INSTRUMENT_DIR/sandbox_runner.sh"

if [ -e "$LINEAGE" ] || [ -e "$GITDIR" ]; then
  echo "init-lineage: LINEAGE and GITDIR must both be new paths" >&2
  exit 2
fi
if [ ! -r "$SEED" ]; then
  echo "init-lineage: canonical organism is missing: $SEED" >&2
  exit 2
fi

mkdir -p -- "$LINEAGE"
git init -q --separate-git-dir="$GITDIR" "$LINEAGE"
git --git-dir="$GITDIR" --work-tree="$LINEAGE" config user.name \
  "${OUROBOROS_GIT_NAME:-ouroboros}"
git --git-dir="$GITDIR" --work-tree="$LINEAGE" config user.email \
  "${OUROBOROS_GIT_EMAIL:-ouroboros@localhost}"

cp -a -- "$SEED" "$LINEAGE/organism.el"
: > "$LINEAGE/journal.md"

fingerprint="$($RUNTIME fingerprint \
  "$INSTRUMENT_DIR/kernel.sh" "$INSTRUMENT_DIR/model_broker.py" \
  "$RUNTIME" "$RUNNER")"
seed_sha256="$(sha256sum "$SEED" | awk '{print $1}')"
"$RUNTIME" write-metadata "$LINEAGE/.ouroboros-lineage.json" \
  "$fingerprint" "$seed_sha256"

git --git-dir="$GITDIR" --work-tree="$LINEAGE" add -A -f
git --git-dir="$GITDIR" --work-tree="$LINEAGE" commit -qm "seed"

echo "initialized lineage: $LINEAGE"
echo "separate git dir:    $GITDIR"
echo "instrument:          $fingerprint"
