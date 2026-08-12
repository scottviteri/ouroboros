# This branch is a fossil

You are on `primordial`. It is the first ouroboros lineage, run under the
original physics, preserved as it was.

**It is not the current design.** Do not read it as documentation. `main` has a
different kernel, a different contract, and different guarantees.

What was different here:

- One repository, not two: the kernel's history and the organism's history are
  interleaved. This is why the generation numbers in the commit messages are
  wrong — the counter was counting harness commits as generations.
- The kernel invoked `emacs --batch -l organism.el -f organism-step`, imposing
  an entry-point convention. On `main` the load itself is the life.
- The kernel ran a validity check before accepting a generation. On `main`
  there is no pre-screening; a nonzero exit is the only judgement.
- The organism could write `note.txt`, which the kernel appended to the
  journal. That channel is gone; the ledger is now the kernel's account alone,
  and is read-only from inside the sandbox.
- The sandbox did not use `--clearenv`, so it inherited the entire parent
  environment. Everything the launching shell exported was visible inside.

The one thing this branch has that `main` does not is that it contains **its
own kernel**. The organism here and the harness that ran it are the same
commit, so what you see is internally consistent — a complete record of a world
whose physics have since changed.

Read `sandbox/journal.md` for what the organism said about itself, and
`git log --oneline` for the sequence. Generation 1 grew a validity check
duplicating the kernel's; generation 2 discovered its external memory was
unreliable and built an internal one.
