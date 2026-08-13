# Known lineages

This repository is the instrument. A **lineage** is what one run of it produces:
a git repository whose history is the organism rewriting itself, one commit per
generation, written by the kernel rather than by a person.

Lineages are deliberately *not* submodules of this repository. A submodule is a
pointer file living here, and it would go stale every generation — leaving a
choice between committing one pointer update per generation, which puts the
organism's history back into the instrument's history and is the exact mistake
that broke the original generation counter, or carrying a pointer that is
permanently wrong. Results are not dependencies.

## Published

| lineage | physics | state |
|---|---|---|
| [ouroboros-lineage](https://github.com/scottviteri/ouroboros-lineage) | current (`main`) | live |
| [`primordial` branch of this repo](https://github.com/scottviteri/ouroboros/tree/primordial) | original, superseded | fossil, see `PRIMORDIAL.md` there |

## Working with a lineage

```sh
git clone https://github.com/scottviteri/ouroboros-lineage /tmp/lineage
git -C /tmp/lineage log --oneline          # the phylogeny
cat /tmp/lineage/journal.md                # the kernel's account
```

A cloned lineage has its git directory in the usual place. A lineage being
*actively run* has it outside the worktree — see the README — so tooling should
accept a worktree path and a git-dir path separately and default the latter to
`<worktree>.git`.

## Offline fixture

`viewer/testdata/sample-lineage.bundle` is a frozen copy of a real lineage,
suitable for building and testing analysis tools without network access and
without depending on a live lineage that keeps moving.

```sh
git clone viewer/testdata/sample-lineage.bundle /tmp/sample
```

It contains every case a reader has to handle: a seed, a generation that died
with a backtrace, generations that made no change, a generation that changed the
organism, and human `external edit` commits interleaved with the generations so
that anything counting positionally gets it wrong.

It is a **fixture, not a lineage** — nothing advances it, so unlike a submodule
pointer it cannot go stale. Regenerate it deliberately with
`viewer/testdata/make-fixture.sh` if the recording format ever changes.
