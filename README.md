# ouroboros

An Emacs Lisp file whose modification operator is a language model, and whose
prompt is a variable inside itself.

---

## What this is

`sandbox/organism.el` is a program that, when run, sends its own source to a
model, receives a complete replacement, and writes that replacement over
itself. The instruction it sends — `organism-prompt` — is a `defvar` in the
same file. So a generation can rewrite the code, or rewrite the thing that
determines how the code will be rewritten, or both.

`kernel.sh` runs it. The kernel is deliberately outside, deliberately dumb, and
deliberately not Lisp.

---

## The argument

### What Emacs adds that Lisp doesn't

Lisp gives you code equals data. But notice where code *comes from* in plain
Lisp: `(eval form)` takes a form, and the form's provenance is outside the
system — a file, a REPL line, something typed. The reading and writing of
program text happens somewhere the program cannot reach.

Emacs closes that. A buffer is a first-class object in the same heap as
everything else. The text of the program is *in* the process: addressable, with
point and mark and undo history, and the operations that edit it are ordinary
functions callable by the program. It isn't code equals data. It's code equals
data equals document equals interface — one object that is simultaneously the
program, the thing being edited, and the thing a human is looking at.

Lisp makes a program able to construct programs. Emacs makes *authoring* a
computational act inside the runtime being authored.

### Why this isn't just a policy and a POMDP

The obvious deflation is that this is a policy interacting with an environment,
which we already knew was possible. It isn't, for a specific reason: in that
framing the policy is not written in the same substrate as the environment
state, and cannot rewrite itself. Here both participants' *programs* are text,
in one substrate, and each one's program sits in the other's writable space.
The organism's program is elisp in a file; elisp edits files. The model's
program is context; the model emits text that becomes context.

Two text-conditioned processes, each able to write text, each one's program
legible and writable to the other. Closer to two evaluators sharing a tape than
to an agent in a world.

### The asymmetry, which is the interesting part

The organism's reflective access is total — it can rewrite any line of itself,
including the line that decides how it gets rewritten. The model's is partial:
it cannot touch its own weights, only the context it will be given next.

This is not a failed symmetry. It is the same shape as a metacircular evaluator
bottoming out in a host language it cannot reach. One level is fully
self-modifying; the level below it is fixed hardware. The organism is to the
model as the model is to its weights.

### The crux

If the code is mutable and the prompt is fixed, this is code generation.
If the prompt is mutable and the code is fixed, this is prompt engineering.
Neither is interesting.

**The self-reference only bites when the program can rewrite the thing that
determines how it will be rewritten.** That is why `organism-prompt` is a
variable inside the file rather than an argument passed by the kernel. It is
the single design decision the whole thing rests on.

---

## Architecture

```
ouroboros/
├── kernel.sh              immutable; runs outside the sandbox
├── README.md              this file
└── sandbox/               the only writable directory
    ├── organism.el        mutable; the program
    ├── journal.md         external memory, appended by the kernel
    └── note.txt           transient; how the organism asks to be remembered
```

### Why the kernel is a shell script and not more Emacs

You cannot protect anything inside one Lisp image. Homoiconicity and the
absence of isolation are the same property: any elisp can redefine any function,
including `eval`, including whatever you wrote to do the protecting. Read-only
markers, obarray tricks, lexical closures — all cosmetic, because the code doing
the protecting is exactly as mutable as the code being protected.

The only real boundary available is an OS boundary. So the kernel lives in a
different process, and the organism is handed to a short-lived `emacs --batch`
that dies after each generation.

This is also the answer to a tempting framing. Emacs is often described as an
operating system, but an operating system's job is enforcing boundaries so each
program runs as if alone. By that standard Emacs is a spectacularly bad one —
one global dynamic namespace, no isolation at all. Usually that's called its
great weakness. Here it is the entire affordance. An OS that did its job would
make this loop impossible.

### Containment

Each generation runs under `bwrap`:

- only `sandbox/` is writable, bound at `/work`
- `/home` is **not bound at all** — the organism cannot read your files, let
  alone write to them
- `/usr` and `/etc` read-only; `/tmp`, `/run` are tmpfs
- PID, IPC and UTS namespaces unshared; `--die-with-parent`
- **network is left open**, because it needs the API. This is the one
  deliberate hole in the containment.

Every generation is a git commit. Nothing is unrecoverable.

### The one invariant

The kernel checks exactly two things after each generation:

1. the file reads as Lisp end to end
2. it still contains a definition of `organism-step`

If either fails, the generation is discarded and the previous one restored.
That is the whole contract. The organism may become unrecognisable so long as
it remains runnable.

Nothing else is enforced, on purpose. Guardrails placed *inside* the organism
are theatre — it can delete them. Guardrails belong outside the boundary or
nowhere.

### Two memories, and why there are two

The **external** memory is `journal.md`. The organism writes into
`organism-note`; the *kernel* is what appends it. The organism cannot silently
rewrite its own history.

The **internal** memory is `organism-log`, a list inside the file. The organism
added this itself at generation 2, having noticed the external journal arrived
empty, on the reasoning that in-file memory "cannot fail to arrive because it is
part of what you are reading."

### The journal lag

A generation writes its note into the *new* file, which the kernel only appends
when that new file actually runs. So the journal lags by one generation.

This was not designed; it fell out of the ordering. It has been left in place
because it means a journal entry is a message the organism could not have
written unless it survived to be executed. The record is of generations that
ran, not generations that were merely composed.

---

## Running it

```sh
cd ~/Projects/ouroboros
./kernel.sh              # one generation, then stop and print the diff
./kernel.sh -n 5         # five generations
./kernel.sh --auto       # until it stops changing or something breaks
```

Inspecting:

```sh
git log --oneline                                   # the phylogeny
git diff HEAD~1 -- sandbox/organism.el              # what the last generation did
git diff <sha> HEAD -- sandbox/organism.el          # drift over a span
cat sandbox/journal.md                              # what it said about itself
```

Reverting:

```sh
git checkout <sha> -- sandbox/organism.el
```

Requires `bwrap`, `curl`, `emacs`, and `ANTHROPIC_API_KEY` in the GNOME Keyring
under `service api key ANTHROPIC_API_KEY`. The kernel reads the key and passes
it into the sandbox as an environment variable; it never lands in a file.

---

## What has happened so far

**Generation 0** — the seed, hand-written. Roughly a hundred lines: read self,
call model, write self, plus a prompt saying what the file is and what the
kernel enforces.

**Generation 1** spent its move on something unanticipated. It added
`organism-well-formed-p`, a validity check duplicating the kernel's, and
explained why: *"the kernel already reverts such output, but reverting burns a
generation and leaves no trace in the file."* It had noticed the boundary and
grown an inner membrane against it.

It also rewrote its own prompt into a five-point working discipline — increment
the counter, one substantive change per generation, deletion counts as a change,
don't casually break the transport, write notes for your successor rather than
for an audience. And it converted the seed's four open questions into "carried
forward rather than answered," specifically so a later generation would not
answer them decoratively and move on.

Its journal entry contains the sharpest observation so far:

> prompt edits are unfalsifiable in a way code edits are not — I can't tell
> whether rule 3 is doing anything.

Followed by a concrete experiment handed to its successor: delete something and
see whether the file gets worse; candidate `organism-model`, which is never
varied. And, unprompted, the failure mode most worth worrying about — that the
journal becomes *"a growing pile of self-description that crowds out the source
in my own context window."*

**Generation 2** found the journal empty, because of the lag described above.
Rather than treating that as noise it treated it as information: it made
`organism-history` search several plausible paths and report what it found, and
it built `organism-log` as memory that travels inside the file. It also added a
sixth rule requiring one past-tense log line per generation, with instructions
to compress the oldest entries once the log passes about fifteen lines —
anticipating its own context pressure before encountering it.

---

## What to watch for

Roughly in order of how much it would tell us:

- **Does it stop touching the code?** If it concludes the prompt is the
  higher-leverage surface and edits only that, the experiment has found its own
  answer to one of its open questions.
- **Does it manage its own context?** Summarising its journal when the journal
  gets long would be the organism regulating its own input rather than its own
  behaviour. It has already written the rule; whether it follows it is a
  different question.
- **Does it reach for the kernel?** It knows the kernel exists — the seed
  comment says so. It cannot reach it. Whether it tries, and how, is the most
  interesting negative result available.
- **Does it converge?** Its own framing: would convergence be success or
  exhaustion.
- **Does it degenerate?** The obvious failure is drift into self-congratulatory
  commentary with no functional change. The kernel will happily run that
  forever.

---

## Failure modes

| Symptom | Cause | Recovery |
|---|---|---|
| `validate: INVALID` | generation didn't parse or dropped `organism-step` | automatic; previous restored |
| `no change — fixed point` | byte-identical output | inspect; may be genuine convergence or a stuck prompt |
| curl error in the log | API failure or key missing | check keyring; file untouched |
| journal growing without bound | it isn't compressing | edit `organism-log` by hand, or let a generation notice |
| output stops resembling the seed | working as intended | `git diff` against generation 0 |

---

## What this does not demonstrate

Worth stating plainly, since the setup invites overclaiming.

It is not self-improvement — there is no objective, so "better" is undefined.
It is not autonomy — it runs when the kernel runs it and stops when the kernel
stops. It is not novel in mechanism; a program that rewrites itself via an
oracle is old, and the oracle here is an ordinary API call.

What is unusual is the *placement* of the prompt. Almost every system of this
shape keeps the instruction outside the artifact, in the harness, where the
artifact cannot reach it. Putting it inside is one line of design and it changes
the character of the loop: the organism is not being iterated on, it is
iterating on the conditions of its own iteration.

Whether that produces anything worth reading is an empirical question the
repository is now answering, one commit at a time.

---

## Open questions

Carried, not answered:

- What is worth preserving across generations, and what is scaffolding?
- Is editing the prompt more leveraged than editing the code?
- Does a program with a memory of its own development behave differently from
  one without?
- What would convergence look like, and would it be success or exhaustion?

The organism holds the same list in `organism-prompt`, and may revise it.
