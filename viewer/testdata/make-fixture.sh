#!/usr/bin/env bash
# make-fixture.sh — freeze a lineage into a git bundle for offline testing.
#
# A bundle is a single file containing full history, and `git clone <bundle>`
# reconstructs a real repository from it. That gives analysis tools something
# concrete to build against with no network and no second checkout, without
# coupling this repository to a lineage that keeps moving.
#
# This is a fixture, not a lineage. Nothing advances it. Regenerate it
# deliberately — and only — if the kernel's recording format changes.
#
#   ./make-fixture.sh [worktree] [gitdir]
#
# Defaults to ~/ouroboros-lineage with its git dir at ~/ouroboros-lineage.git,
# which is how a lineage is laid out while it is being run.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
WT="${1:-$HOME/ouroboros-lineage}"
GD="${2:-$WT.git}"
OUT="$HERE/sample-lineage.bundle"

[ -d "$GD" ] || { echo "no git dir at $GD" >&2; exit 1; }

G() { git --git-dir="$GD" --work-tree="$WT" "$@"; }

echo "source:      $WT (git dir $GD)"
echo "commits:     $(G rev-list --count HEAD)"
echo "generations: $(G rev-list --count --grep='^gen ' HEAD)"
echo

# --all so every branch is captured; a lineage's runs are branches.
G bundle create "$OUT" --all
echo
echo "wrote $OUT ($(du -h "$OUT" | cut -f1))"

# A bundle that cannot be cloned is worse than no bundle, so prove it.
TMP=$(mktemp -d)
git clone -q "$OUT" "$TMP/check"
echo
echo "verification — cloned from the bundle:"
git -C "$TMP/check" log --oneline | sed 's/^/  /'
echo
echo "  generations by the counting rule: $(git -C "$TMP/check" rev-list --count --grep='^gen ' HEAD)"
echo "  journal entries:                  $(grep -c '^## gen ' "$TMP/check/journal.md" 2>/dev/null || echo 0)"
echo "  files at HEAD:"
git -C "$TMP/check" ls-files | sed 's/^/    /'
rm -rf "$TMP"
