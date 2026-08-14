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

tail -n 5 /run/organism.log >&2
exit "$rc"
