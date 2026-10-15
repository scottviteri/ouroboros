#!/bin/sh
# Runs inside bwrap. /work is a size-limited tmpfs and stdout is the result channel.
set -u

tar -xf /kernel/base.tar -C /work || exit 70
rm -f /work/.git /work/.ouroboros-lineage.json /work/journal.md

emacs -Q --batch -l /work/organism.el > /run/organism.log 2>&1
rc=$?

if [ "$rc" -eq 0 ]; then
  # Kernel-owned paths cannot be smuggled back into the committed worktree.
  rm -rf /work/.git /work/.ouroboros-lineage.json /work/journal.md
  if find /work \( -type b -o -type c -o -type p -o -type s \) -print -quit |
      grep -q .; then
    echo "sandbox runner: unsupported special filesystem object" >&2
    exit 73
  fi
  tar --format=posix --hard-dereference -C /work -cf - .
  exit $?
fi

# Hand the kernel a bounded copy of the whole log, not just its end: Emacs
# prints the error before the backtrace, so a deep backtrace pushes the error
# out of any short tail. The kernel journal still takes only the last five
# lines; the rest is for the trusted observer.
size=$(wc -c < /run/organism.log)
if [ "$size" -le 32768 ]; then
  cat /run/organism.log >&2
else
  head -c 16384 /run/organism.log >&2
  printf '\n[sandbox runner: %s bytes omitted]\n' "$((size - 32768))" >&2
  tail -c 16384 /run/organism.log >&2
fi
exit "$rc"
