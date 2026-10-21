# Design note: an Emacs buffer world

*Proposal recorded 2026-10-21. No change to the running instrument or seed is
implied by this note.*

## The distinction

The present kernel already publishes the resulting `/work` filesystem after a
generation exits zero. It does not require the organism to produce only one
file. The current **seed**, however, sends its complete source to the model and
uses the reply as the complete next `organism.el`. Its reproduction method is
effectively *source file in, replacement source file out*.

An Emacs-centered variant would treat the running Emacs process as the living
body and buffers as the surfaces it can inspect and edit. The source of
`organism.el` would be a live buffer visiting a file in `/work`. A model reply
could enter another buffer; the organism's own editable code would decide how
to use it: edit a region of its body, change the prompt or editing procedure,
write a memory, evaluate a definition for immediate use, or do nothing. The
next generation would inherit the filesystem left by successful saved edits,
not the live Emacs heap.

That gives three distinct kinds of state:

- **Live state:** buffers, point, markers, undo history, variable values, and
  evaluated function definitions in this generation's Emacs process.
- **Inherited state:** source and other data the organism leaves in `/work` when
  the generation exits zero. A saved `organism.el` builds the next body; other
  files may be memories or things the organism built. An unsaved buffer edit
  vanishes with the process. Saving source makes a function definition
  inheritable; merely evaluating it changes only the current life.
- **External record:** the kernel's journal and Git history. They record what
  happened without being writable by the organism as trusted history. A failed
  generation's speculative filesystem writes are discarded.

This is a choice of experimental substrate, not a claim that plain Lisp cannot
edit raw source. Lisp can do that. Emacs makes a mutable document, with both
character-level and syntax-aware editing operations, a native runtime object.
The useful analogy to [*Computational Life*](https://arxiv.org/abs/2406.19108)
is that executable instructions and writable representation can coincide. The
paper studies the emergence of replicators from interacting random programs;
Ouroboros starts with a hand-written replicating seed and a model capability,
so it addresses a different question.

## Boundaries and possible next experiment

Emacs read-only flags are not a security boundary. Keep the trusted kernel,
broker, OS sandbox, resource limits, and zero-exit publication rule outside the
organism. Do not have the kernel automatically preserve all buffers or function
cells: deciding what to save can be organism behavior. The organism may write
additional source files and load them in later generations if it chooses.

A later experiment could proceed in the **instrument** repository:

1. Describe the live-state/inherited-state boundary in the main README.
2. Make the canonical seed visit its own source as a body buffer and receive
   model text in a separate buffer, initially without changing behavior.
3. Change only the seed's initial reproduction method from wholesale replacement
   to edits applied to the body buffer. Keep the prompt and editing method in
   that same editable source. The choice of edit language and when to save are
   open design questions, not kernel rules.
4. Check saved versus unsaved edits, live versus inherited definitions, and
   death rollback; then start a **new lineage** pinned to that instrument
   commit. Record its generations and trusted observations in separate branches
   of the `ouroboros-lineage` repository. Existing lineages retain their
   original instrument versions.

Comparing this seed with the current one could show whether a buffer-based
editing method changes the kinds of self-modification observed. Neither
survival nor more edits alone would establish self-improvement; the interesting
question is whether the organism changes the procedure that determines its
later changes.
