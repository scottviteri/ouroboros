#!/usr/bin/env bash
# kernel.sh — immutable harness. Lives in the kernel repo; runs OUTSIDE the sandbox.
# Contract: one generation = one load of organism.el = exactly one lineage commit.
#
# The kernel supplies senses, never answers: it records raw observable fact and
# offers no instructions, budgets or advice. The only judgement it makes is exit
# status, and the only thing it enforces is that a nonzero exit changes nothing.
set -euo pipefail

LINEAGE="${LINEAGE:?path to lineage worktree}"     # e.g. ~/ouroboros-lineage
GITDIR="${GITDIR:-$LINEAGE.git}"                   # separate git dir, OUTSIDE the worktree
JOURNAL="$LINEAGE/journal.md"
GENERATIONS="${GENERATIONS:-10}"
WALL="${WALL:-600}"                                # wallclock seconds per generation
CPU="${CPU:-120}"                                  # CPU seconds per generation
LOG="$(mktemp)"

G() { git --git-dir="$GITDIR" --work-tree="$LINEAGE" "$@"; }

# --- environment detection --------------------------------------------------
# The sandbox has to work on at least two very different hosts: Arch, where
# /etc/ssl/certs/ca-certificates.crt is a symlink into /etc/ca-certificates,
# and Debian/Ubuntu, where it is a real file. Binding only /etc/ssl on Arch
# gives `curl: (77) error adding trust anchors` and the organism then dies
# every generation for a reason that has nothing to do with it — which would
# fill the journal with deaths the harness caused.
#
# --ro-bind-try is a no-op when the path is absent, so the same invocation
# covers both. These are collected as an array so `--doctor` can report what
# it resolved to rather than leaving it implicit.
CERT_BINDS=()
for p in /etc/ca-certificates /etc/pki /etc/nsswitch.conf /etc/hosts; do
  [ -e "$p" ] && CERT_BINDS+=(--ro-bind-try "$p" "$p")
done

doctor() {
  echo "host:        $( . /etc/os-release 2>/dev/null; echo "${PRETTY_NAME:-unknown}")"
  echo "emacs:       $(emacs --version 2>/dev/null | head -1 || echo MISSING)"
  echo "bwrap:       $(command -v bwrap || echo MISSING)"
  echo "curl:        $(command -v curl || echo MISSING)"
  echo "git:         $(command -v git || echo MISSING)"
  echo "ca bundle:   $(readlink -f /etc/ssl/certs/ca-certificates.crt 2>/dev/null || echo 'not found')"
  echo "extra binds: ${CERT_BINDS[*]:-none}"
  echo "userns:      $(sysctl -n kernel.unprivileged_userns_clone 2>/dev/null || echo 'n/a (usually enabled)')"
  echo "API key:     $([ -n "${ANTHROPIC_API_KEY:-}" ] && echo "present (${#ANTHROPIC_API_KEY} chars)" || echo MISSING)"
  echo "lineage:     $LINEAGE  ($([ -d "$LINEAGE" ] && echo exists || echo MISSING))"
  echo "git dir:     $GITDIR  ($([ -d "$GITDIR" ] && echo exists || echo MISSING))"
  echo -n "tls reachability: "
  if bwrap --clearenv --setenv PATH /usr/bin:/bin \
       --ro-bind /usr /usr --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
       --symlink usr/bin /bin --symlink usr/sbin /sbin \
       --ro-bind /etc/resolv.conf /etc/resolv.conf --ro-bind /etc/ssl /etc/ssl \
       "${CERT_BINDS[@]}" \
       --proc /proc --dev /dev --tmpfs /tmp --unshare-pid --die-with-parent \
       curl -sS -o /dev/null --max-time 20 https://api.anthropic.com/ 2>/dev/null
  then echo "ok"; else echo "FAILED — the organism would die every generation"; fi
}

if [ "${1:-}" = "--doctor" ]; then doctor; exit 0; fi

for _ in $(seq "$GENERATIONS"); do
  # absorb out-of-band (human) edits so each generation starts from a committed state
  G add -A
  G diff --cached --quiet || G commit -qm "external edit"

  gen=$(( $(G rev-list --count --grep='^gen ' HEAD) + 1 ))

  set +e
  timeout "$WALL" bwrap \
    --clearenv \
    --setenv ANTHROPIC_API_KEY "${ANTHROPIC_API_KEY:?}" \
    --setenv HOME /work --setenv PATH /usr/bin:/bin \
    --ro-bind /usr /usr \
    --symlink usr/lib /lib --symlink usr/lib64 /lib64 \
    --symlink usr/bin /bin --symlink usr/sbin /sbin \
    --ro-bind /etc/resolv.conf /etc/resolv.conf \
    --ro-bind /etc/ssl /etc/ssl \
    "${CERT_BINDS[@]}" \
    --proc /proc --dev /dev --tmpfs /tmp --tmpfs /run \
    --bind "$LINEAGE" /work \
    --ro-bind "$JOURNAL" /work/journal.md \
    --unshare-pid --unshare-ipc --unshare-uts \
    --die-with-parent \
    sh -c "ulimit -t $CPU; exec emacs -Q --batch -l /work/organism.el" \
    >"$LOG" 2>&1
  rc=$?
  set -e

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

rm -f "$LOG"
