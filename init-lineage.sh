#!/usr/bin/env bash
# Create a new lineage from the canonical organism owned by this instrument.
set -euo pipefail

INSTRUMENT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LINEAGE="${LINEAGE:?path for the new lineage worktree}"
GITDIR="${GITDIR:-$LINEAGE.git}"
LINEAGE_BRANCH="${LINEAGE_BRANCH:-$(basename -- "$LINEAGE")}"
OBSERVATION="${OBSERVATION:-$LINEAGE.observations}"
OBSERVATION_BRANCH="${OBSERVATION_BRANCH:-observations/$LINEAGE_BRANCH}"
SEED="$INSTRUMENT_DIR/organism.el"
RUNTIME="$INSTRUMENT_DIR/runtime.py"
RUNNER="$INSTRUMENT_DIR/sandbox_runner.sh"

if [ -e "$LINEAGE" ] || [ -e "$GITDIR" ] || [ -e "$OBSERVATION" ]; then
  echo "init-lineage: LINEAGE, GITDIR, and OBSERVATION must all be new paths" >&2
  exit 2
fi
if [ ! -r "$SEED" ]; then
  echo "init-lineage: canonical organism is missing: $SEED" >&2
  exit 2
fi
if ! git check-ref-format --branch "$LINEAGE_BRANCH" >/dev/null 2>&1 || \
   ! git check-ref-format --branch "$OBSERVATION_BRANCH" >/dev/null 2>&1; then
  echo "init-lineage: invalid lineage or observation branch name" >&2
  exit 2
fi

instrument_commit="${OUROBOROS_INSTRUMENT_COMMIT:-}"
instrument_ref="${OUROBOROS_INSTRUMENT_REF:-}"
instrument_repository="${OUROBOROS_INSTRUMENT_REPOSITORY:-}"
if [ -z "$instrument_commit" ]; then
  if ! git -C "$INSTRUMENT_DIR" diff --quiet -- || \
     ! git -C "$INSTRUMENT_DIR" diff --cached --quiet --; then
    echo "init-lineage: instrument checkout has uncommitted tracked changes" >&2
    exit 2
  fi
  instrument_commit="$(git -C "$INSTRUMENT_DIR" rev-parse --verify HEAD)"
  instrument_ref="${instrument_ref:-$(git -C "$INSTRUMENT_DIR" symbolic-ref --short -q HEAD || printf detached)}"
  instrument_repository="${instrument_repository:-$(git -C "$INSTRUMENT_DIR" remote get-url origin 2>/dev/null || printf local)}"
fi
if ! [[ "$instrument_commit" =~ ^[0-9a-f]{40}$|^[0-9a-f]{64}$ ]]; then
  echo "init-lineage: instrument commit must be a full hexadecimal Git object ID" >&2
  exit 2
fi
instrument_ref="${instrument_ref:-detached}"
instrument_repository="${instrument_repository:-local}"

mkdir -p -- "$LINEAGE"
git init -q --initial-branch="$LINEAGE_BRANCH" \
  --separate-git-dir="$GITDIR" "$LINEAGE"
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
  "$fingerprint" "$seed_sha256" "$instrument_repository" \
  "$instrument_ref" "$instrument_commit"

git --git-dir="$GITDIR" --work-tree="$LINEAGE" add -A -f
git --git-dir="$GITDIR" --work-tree="$LINEAGE" commit -qm "seed"

git init -q --initial-branch="$OBSERVATION_BRANCH" "$OBSERVATION"
git -C "$OBSERVATION" config user.name "${OUROBOROS_GIT_NAME:-ouroboros}"
git -C "$OBSERVATION" config user.email \
  "${OUROBOROS_GIT_EMAIL:-ouroboros@localhost}"
"$RUNTIME" write-observation-metadata "$OBSERVATION/metadata.json" \
  "$LINEAGE_BRANCH" "$OBSERVATION_BRANCH" "$instrument_repository" "$instrument_ref" \
  "$instrument_commit" "$fingerprint" "$seed_sha256"
git -C "$OBSERVATION" add metadata.json
git -C "$OBSERVATION" commit -qm "observe: initialize $LINEAGE_BRANCH"

echo "initialized lineage: $LINEAGE"
echo "separate git dir:    $GITDIR"
echo "lineage branch:      $LINEAGE_BRANCH"
echo "observations:        $OBSERVATION ($OBSERVATION_BRANCH)"
echo "instrument commit:   $instrument_commit"
echo "instrument digest:   $fingerprint"
