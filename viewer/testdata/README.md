# testdata

## sample-lineage.bundle

A frozen copy of a real lineage, as a git bundle — one file holding the full
history of every branch.

```sh
git clone viewer/testdata/sample-lineage.bundle /tmp/sample
git -C /tmp/sample log --oneline
cat /tmp/sample/journal.md
```

Use it to build and test analysis tools offline and deterministically, rather
than against a live lineage that keeps advancing under you.

### What it covers

Every case a reader of a lineage has to handle:

- **`seed`** — the first commit, containing the initial organism snapshot.
- **`gen N: died (exit R); reverted`** — a generation that failed to load. The
  journal entry carries the exit code and the tail of stderr, verbatim. This is
  the most information-dense entry type and the easiest to render badly.
- **`gen N: no-change`** — the organism ran, exited zero, and altered nothing.
  Committed with `--allow-empty` so the count stays honest.
- **`gen N: changed +A/-B`** — the organism rewrote itself.
- **`external edit: ...`** — a change entering the worktree from outside the
  generation loop.

That last one is the trap. External edits interleave with generations, so
**generation number is the count of commits whose subject starts with `gen `**,
never a positional index. Anything that counts by position will disagree with
`journal.md`, and the journal is right.

### Why a bundle rather than a submodule

A submodule is a pointer that lives in the parent repository and goes stale
every time the lineage advances — forcing a choice between committing a pointer
update per generation, which re-interleaves the two histories, or carrying a
pointer that is wrong. A fixture has no such problem because nothing advances
it. See `LINEAGES.md` at the repository root.

### Regenerating

Only if the kernel's recording format changes:

```sh
viewer/testdata/make-fixture.sh [worktree] [gitdir]
```

It verifies its own output by cloning the bundle back and printing the history,
because a bundle that cannot be cloned is worse than no bundle.
