#!/usr/bin/env bash
# kernel.sh — immutable harness. Lives in the kernel repo; runs OUTSIDE the sandbox.
# Contract: one generation = one load of organism.el = exactly one lineage commit.
#
# The kernel mediates capabilities but supplies no content of its own. The
# organism can ask an opaque model service for text; provider credentials and
# protocol details remain outside. The kernel records raw observable fact, and
# its only judgement is exit status: a nonzero exit changes nothing.
set -euo pipefail

KERNEL_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
LINEAGE="${LINEAGE:?path to lineage worktree}"     # e.g. ~/ouroboros-lineage
GITDIR="${GITDIR:-$LINEAGE.git}"                   # separate git dir, OUTSIDE the worktree
JOURNAL="$LINEAGE/journal.md"
GENERATIONS="${GENERATIONS:-10}"
WALL="${WALL:-600}"                                # wallclock seconds per generation
CPU="${CPU:-120}"                                  # CPU seconds per generation
MODEL_PROVIDER="${MODEL_PROVIDER:-anthropic}"
MODEL_NAME="${MODEL_NAME:-}"
MODEL_MAX_OUTPUT_TOKENS="${MODEL_MAX_OUTPUT_TOKENS:-12000}"
MODEL_REQUEST_TIMEOUT="${MODEL_REQUEST_TIMEOUT:-600}"
MODEL_MAX_PROMPT_BYTES="${MODEL_MAX_PROMPT_BYTES:-4194304}"
PYTHON="${PYTHON:-python3}"
MODEL_BROKER="$KERNEL_DIR/model_broker.py"

G() { git --git-dir="$GITDIR" --work-tree="$LINEAGE" "$@"; }

case "$MODEL_PROVIDER" in
  anthropic)
    API_KEY_NAME=ANTHROPIC_API_KEY
    API_ORIGIN=https://api.anthropic.com
    MODEL_NAME="${MODEL_NAME:-claude-opus-5}"
    ;;
  openai)
    API_KEY_NAME=OPENAI_API_KEY
    API_ORIGIN=https://api.openai.com
    MODEL_NAME="${MODEL_NAME:-gpt-5.6}"
    ;;
  *)
    echo "kernel: MODEL_PROVIDER must be 'anthropic' or 'openai'" >&2
    exit 2
    ;;
esac

API_KEY="${!API_KEY_NAME:-}"
LOG="$(mktemp)"
BROKER_LOG="$(mktemp)"
BROKER_PID=""
BROKER_DIR=""
BROKER_SOCKET=""

stop_broker() {
  if [ -n "$BROKER_PID" ]; then
    kill "$BROKER_PID" 2>/dev/null || true
    wait "$BROKER_PID" 2>/dev/null || true
    BROKER_PID=""
  fi
  if [ -n "$BROKER_SOCKET" ]; then
    rm -f -- "$BROKER_SOCKET"
    BROKER_SOCKET=""
  fi
  if [ -n "$BROKER_DIR" ]; then
    rmdir -- "$BROKER_DIR" 2>/dev/null || true
    BROKER_DIR=""
  fi
}

cleanup() {
  stop_broker
  rm -f -- "$LOG" "$BROKER_LOG"
}
trap cleanup EXIT

start_broker() {
  stop_broker
  : > "$BROKER_LOG"
  BROKER_DIR="$(mktemp -d)"
  BROKER_SOCKET="$BROKER_DIR/model.sock"

  # Configuration, including the selected credential, crosses a private pipe
  # into the trusted broker. It never appears in argv, a file, or the sandbox.
  {
    printf '%s\n' "$MODEL_PROVIDER"
    printf '%s\n' "$MODEL_NAME"
    printf '%s\n' "$API_KEY"
    printf '%s\n' "$MODEL_MAX_OUTPUT_TOKENS"
    printf '%s\n' "$MODEL_REQUEST_TIMEOUT"
    printf '%s\n' "$MODEL_MAX_PROMPT_BYTES"
  } | env -i PATH="$PATH" "$PYTHON" "$MODEL_BROKER" --socket "$BROKER_SOCKET" \
        >"$BROKER_LOG" 2>&1 &
  BROKER_PID=$!

  for _ in {1..100}; do
    [ -S "$BROKER_SOCKET" ] && return 0
    if ! kill -0 "$BROKER_PID" 2>/dev/null; then
      echo "kernel: model broker exited during startup" >&2
      tail -n 10 "$BROKER_LOG" >&2
      stop_broker
      return 1
    fi
    sleep 0.05
  done

  echo "kernel: model broker did not become ready" >&2
  tail -n 10 "$BROKER_LOG" >&2
  stop_broker
  return 1
}

doctor() {
  echo "host:        $( . /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")"
  echo "emacs:       $(emacs --version 2>/dev/null | head -1 || echo MISSING)"
  echo "bwrap:       $(command -v bwrap || echo MISSING)"
  echo "curl:        $(command -v curl || echo MISSING)"
  echo "git:         $(command -v git || echo MISSING)"
  echo "python:      $("$PYTHON" --version 2>&1 || echo MISSING)"
  echo "userns:      $(sysctl -n kernel.unprivileged_userns_clone 2>/dev/null || echo 'n/a (usually enabled)')"
  echo "provider:    $MODEL_PROVIDER (kernel-only)"
  echo "model:       $MODEL_NAME (kernel-only)"
  echo "$API_KEY_NAME: $([ -n "$API_KEY" ] && echo "present (${#API_KEY} chars)" || echo MISSING)"
  echo "lineage:     $LINEAGE  ($([ -d "$LINEAGE" ] && echo exists || echo MISSING))"
  echo "git dir:     $GITDIR  ($([ -d "$GITDIR" ] && echo exists || echo MISSING))"
  echo -n "host API reachability: "
  if curl -sS -o /dev/null --max-time 20 "$API_ORIGIN/" 2>/dev/null
  then echo "ok"; else echo "FAILED"; fi

  echo -n "sandbox model syscall: "
  if [ -z "$API_KEY" ]; then
    echo "SKIPPED — $API_KEY_NAME is missing"
  elif start_broker &&
       bwrap --clearenv --setenv HOME /work --setenv PATH /usr/bin:/bin \
         --ro-bind /usr /usr \
         --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
         --symlink usr/bin /bin --symlink usr/sbin /sbin \
         --proc /proc --dev /dev --tmpfs /tmp --tmpfs /run \
         --ro-bind "$BROKER_DIR" /kernel \
         --unshare-net --unshare-pid --unshare-ipc --unshare-uts \
         --die-with-parent \
         curl -sS --max-time 5 --unix-socket /kernel/model.sock \
           http://kernel/__probe__ >/dev/null 2>&1
  then
    echo "ok (IP network disabled)"
  else
    echo "FAILED"
  fi
  stop_broker
}

if [ "${1:-}" = "--doctor" ]; then doctor; exit 0; fi

if [ -z "$API_KEY" ]; then
  echo "kernel: $API_KEY_NAME is required for MODEL_PROVIDER=$MODEL_PROVIDER" >&2
  exit 2
fi
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "kernel: $PYTHON is required for the model broker" >&2
  exit 2
fi
if [ ! -r "$MODEL_BROKER" ]; then
  echo "kernel: model broker not found at $MODEL_BROKER" >&2
  exit 2
fi

for _ in $(seq "$GENERATIONS"); do
  # absorb out-of-loop edits so each generation starts from a committed state
  G add -A
  G diff --cached --quiet || G commit -qm "external edit"

  gen=$(( $(G rev-list --count --grep='^gen ' HEAD) + 1 ))

  : > "$LOG"
  start_broker

  set +e
  timeout "$WALL" bwrap \
    --clearenv \
    --setenv HOME /work --setenv PATH /usr/bin:/bin \
    --ro-bind /usr /usr \
    --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
    --symlink usr/bin /bin --symlink usr/sbin /sbin \
    --proc /proc --dev /dev --tmpfs /tmp --tmpfs /run \
    --ro-bind "$BROKER_DIR" /kernel \
    --bind "$LINEAGE" /work \
    --ro-bind "$JOURNAL" /work/journal.md \
    --unshare-net --unshare-pid --unshare-ipc --unshare-uts \
    --die-with-parent \
    sh -c "ulimit -t $CPU; exec emacs -Q --batch -l /work/organism.el" \
    >"$LOG" 2>&1
  rc=$?
  set -e
  stop_broker

  if [ "$rc" -ne 0 ]; then
    # DEATH: discard every write this generation made; restore last surviving organism
    G checkout -q HEAD -- .
    G clean -qfd
    prev=$(G rev-list -n 2 HEAD -- organism.el | tail -1)
    G checkout -q "$prev" -- organism.el
    { echo
      echo "## gen $gen — died — $(date -Is)"
      echo "exit $rc"
      echo '~~~'
      tail -n 5 "$LOG"
      echo '~~~'
    } >> "$JOURNAL"
    G add -A
    G commit -qm "gen $gen: died (exit $rc); reverted"
    continue
  fi

  if [ -z "$(G status --porcelain)" ]; then
    printf '\n## gen %s — no-change — %s\n' "$gen" "$(date -Is)" >> "$JOURNAL"
    G add -A
    G commit -q --allow-empty -m "gen $gen: no-change"
  else
    stat=$(G diff --numstat -- organism.el | awk '{print "+"$1"/-"$2}')
    printf '\n## gen %s — changed %s — %s\n' "$gen" "${stat:-+0/-0}" "$(date -Is)" >> "$JOURNAL"
    G add -A
    G commit -qm "gen $gen: changed ${stat:-+0/-0}"
  fi
done
