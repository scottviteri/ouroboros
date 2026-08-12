#!/usr/bin/env bash
# kernel.sh — the immutable part.
#
# Why this is a shell script outside the sandbox rather than more elisp:
# within one Lisp image there is no way to protect anything. Homoiconicity and
# the absence of isolation are the same property — any elisp can redefine any
# function including `eval'. The only real boundary available is an OS
# boundary, so the kernel lives in a different process, and the mutable file is
# handed to a short-lived `emacs --batch' that dies after each generation.
#
# The kernel guarantees exactly three things and deliberately nothing else:
#   1. the organism runs with only ./sandbox writable and no access to $HOME
#   2. every generation is a git commit, so nothing is unrecoverable
#   3. if a generation leaves organism.el unparseable, or without an
#      `organism-step', the previous generation is restored
#
# Everything else — how it prompts, what it remembers, what it becomes — is
# the organism's business.
#
#   ./kernel.sh              one generation, then stop and show the diff
#   ./kernel.sh -n 5         five generations
#   ./kernel.sh --auto       until it stops changing or something breaks
set -uo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
SANDBOX="$ROOT/sandbox"
ORGANISM="$SANDBOX/organism.el"
JOURNAL="$SANDBOX/journal.md"
LOG="$ROOT/kernel.log"

STEPS=1
[ "${1:-}" = "-n" ] && { STEPS="${2:-1}"; }
[ "${1:-}" = "--auto" ] && STEPS=999

KEY=$(secret-tool lookup service api key ANTHROPIC_API_KEY 2>/dev/null)
[ -n "$KEY" ] || KEY="${ANTHROPIC_API_KEY:-}"
[ -n "$KEY" ] || { echo "no ANTHROPIC_API_KEY in the keyring or environment" >&2; exit 1; }

say() { printf '%s\n' "$*" | tee -a "$LOG"; }

# A generation is valid if it parses and still defines organism-step. Nothing
# else is checked — the organism is free to become unrecognisable so long as it
# remains runnable.
validate() {
  emacs -Q --batch --eval "(condition-case e
      (let ((forms 0))
        (with-temp-buffer
          (insert-file-contents \"$ORGANISM\")
          (goto-char (point-min))
          (condition-case nil (while t (read (current-buffer)) (setq forms (1+ forms))) (end-of-file nil)))
        (with-temp-buffer
          (insert-file-contents \"$ORGANISM\")
          (if (re-search-forward \"(defun organism-step\" nil t)
              (message \"VALID forms=%d\" forms)
            (message \"INVALID no organism-step\"))))
    (error (message \"INVALID %S\" e)))" 2>&1 | tail -1
}

cd "$ROOT" || exit 1
git add -A >/dev/null 2>&1
git diff --cached --quiet || git commit -q -m "state before run $(date -Is)"

for i in $(seq 1 "$STEPS"); do
  gen=$(git rev-list --count HEAD 2>/dev/null || echo 0)
  say ""
  say "=== generation $gen ($(date +%H:%M:%S)) ==="
  before=$(md5sum "$ORGANISM" | cut -d' ' -f1)

  # Only ./sandbox is writable. /home is not bound at all, so the organism
  # cannot read Scott's files, let alone write to them. Network is left open
  # because it needs the API; that is the deliberate hole.
  bwrap \
    --ro-bind /usr /usr --ro-bind /etc /etc \
    --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
    --symlink usr/bin /bin --symlink usr/sbin /sbin \
    --proc /proc --dev /dev --tmpfs /tmp --tmpfs /run \
    --bind "$SANDBOX" /work --chdir /work \
    --setenv HOME /work \
    --setenv ANTHROPIC_API_KEY "$KEY" \
    --unshare-pid --unshare-ipc --unshare-uts --die-with-parent \
    emacs -Q --batch -l /work/organism.el -f organism-step 2>&1 | tail -3 | tee -a "$LOG"

  v=$(validate)
  say "  validate: $v"
  if [[ "$v" != VALID* ]]; then
    say "  !! rejecting this generation, restoring previous"
    git checkout -- "$ORGANISM"
    break
  fi

  after=$(md5sum "$ORGANISM" | cut -d' ' -f1)
  if [ "$before" = "$after" ]; then
    say "  no change — fixed point reached"
    break
  fi

  # The organism asks to be remembered by writing note.txt; the kernel is what
  # actually appends it, so the journal cannot be silently rewritten.
  if [ -s "$SANDBOX/note.txt" ]; then
    { echo; echo "## generation $gen — $(date -Is)"; cat "$SANDBOX/note.txt"; } >> "$JOURNAL"
    rm -f "$SANDBOX/note.txt"
    say "  journal appended"
  fi

  git add -A >/dev/null 2>&1
  git commit -q -m "generation $gen"
  say "  committed ($(wc -l < "$ORGANISM") lines)"
done

say ""
say "=== diff of the last generation ==="
git --no-pager diff HEAD~1 -- sandbox/organism.el 2>/dev/null | head -80 | tee -a "$LOG"
say ""
say "history: git -C $ROOT log --oneline"
say "revert:  git -C $ROOT checkout <sha> -- sandbox/organism.el"
