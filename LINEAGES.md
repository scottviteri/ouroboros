# Known lineages

This repository is the instrument. A **lineage** is what one run of it produces:
a git repository whose history is the organism rewriting itself, one commit per
generation, with each result recorded by the kernel.

Lineages are deliberately *not* submodules of this repository. A submodule is a
pointer file living here, and it would go stale every generation — leaving a
choice between committing one pointer update per generation, which puts the
organism's history back into the instrument's history and is the exact mistake
that broke the original generation counter, or carrying a pointer that is
permanently wrong. Results are not dependencies.

## Published

| lineage | physics | state |
|---|---|---|
| [`lineage-original`](https://github.com/scottviteri/ouroboros-lineage/tree/lineage-original) | pre-fingerprinted, direct-worktree physics | historical lineage formerly stored on remote `main` |
| [`lineage-gpt-02`](https://github.com/scottviteri/ouroboros-lineage/tree/lineage-gpt-02) | fingerprinted, bounded disposable worktree | completed under instrument `c9b1deb`; do not extend with a different commit |
| [`lineage-claude-01`](https://github.com/scottviteri/ouroboros-lineage/tree/lineage-claude-01) | fingerprinted, bounded disposable worktree | completed under instrument `c9b1deb`; do not extend with a different commit |
| `lineage-gpt-03` | exact-commit-pinned, disclosed constraints, trusted observations | next GPT run |
| `lineage-claude-02` | exact-commit-pinned, disclosed constraints, trusted observations | next Claude run |
| [`primordial` branch of this repo](https://github.com/scottviteri/ouroboros/tree/primordial) | original, superseded | fossil, see `PRIMORDIAL.md` there |

The lineage repository's remote `main` contains only its viewer-facing README.
Hereditary histories live on `lineage-*` branches; trusted external records live
on matching `observations/lineage-*` branches. Current instrument code refuses
to run a lineage unless both roots identify its exact instrument commit and
trusted-runtime fingerprint. Start a new result with `init-lineage.sh`; do not
migrate a historical lineage onto a new kernel.

## Working with a lineage

```sh
git clone --branch lineage-gpt-03 \
  https://github.com/scottviteri/ouroboros-lineage /tmp/lineage
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
organism, and out-of-loop `external edit` commits interleaved with the
generations so that anything counting positionally gets it wrong.

It is a **fixture, not a lineage** — nothing advances it, so unlike a submodule
pointer it cannot go stale. Regenerate it deliberately with
`viewer/testdata/make-fixture.sh` if the recording format ever changes.
