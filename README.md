# ouroboros

An Emacs Lisp file whose modification operator is a language model, and whose
prompt is a variable inside itself.

This repository is the **instrument**. The lineages it produces live in separate
repositories because the instrument and its experimental records occupy
different layers.

---

## What this is

`organism.el` is a program that, when loaded, sends its own source through a
kernel model syscall, receives a complete replacement, and writes that
replacement over itself. The instruction it sends — `organism-prompt` — is a
`defvar` in the same file. So a generation can rewrite the code, or rewrite the
thing that determines how the code will be rewritten, or both.

`kernel.sh` runs it and mediates its capabilities. The kernel is deliberately
outside, deliberately dumb, and deliberately not Lisp.

The design goal is **emergence under constraint**: the kernel exposes a small,
stable interface and never contributes cognitive content of its own. No
instructions, no advice, and no provider-specific authority enters the organism.

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

Two text-conditioned processes, each able to write text, each one's program
legible and writable to the other. Closer to two evaluators sharing a tape than
to an agent in a world.

### The asymmetry, which is the interesting part

The organism's reflective access is total — it can rewrite any line of itself,
including the line that decides how it gets rewritten. The model's is partial:
it cannot touch its own weights, only the context it will be given next.

This is not a failed symmetry. It is the same shape as a metacircular evaluator
bottoming out in a host language it cannot reach. One level is fully
self-modifying; the level below it is fixed hardware.

### The crux

If the code is mutable and the prompt is fixed, this is code generation.
If the prompt is mutable and the code is fixed, this is prompt engineering.
Neither is interesting.

**The self-reference only bites when the program can rewrite the thing that
determines how it will be rewritten.** That is why `organism-prompt` is a
variable inside the file rather than an argument passed by the kernel. It is
the single design decision the whole thing rests on.

---

## Two repositories, two layers

```
ouroboros/                  # this repo — the instrument, outside the generation loop
  kernel.sh
  README.md
  analysis/                 # run notes (gitignored by default)

ouroboros-lineage/          # a lineage — the generation-loop record, separate repo
  organism.el               # the seed, then whatever it becomes
  journal.md                # kernel-appended, read-only inside the sandbox

ouroboros-lineage.git/      # separate git dir, OUTSIDE the worktree
```

Interleaving the two histories is what broke the original generation counter:
out-of-loop commits and generation commits were being counted together. They
record different layers of the system and belong apart.

The lineage's git directory lives outside the bind-mounted worktree via
`git init --separate-git-dir`. If `.git` rode into the writable mount, the
organism could read and rewrite kernel-authority memory.

Runs are branches of a lineage repo, all sharing the seed commit, never merged.
Lineages are results, not code; merging one into the instrument's history would
be pasting the lab notebook into the firmware.

Lineages are deliberately **not submodules**. A submodule is a pointer file
living in this repository, and it would go stale every generation — leaving a
choice between one pointer commit per generation, which puts the organism's
history back into the instrument's and is exactly the mistake that broke the
counter, or a pointer that is permanently wrong. Results are not dependencies.

`LINEAGES.md` lists the published ones. For building analysis tools without a
network or a second checkout, `viewer/testdata/sample-lineage.bundle` is a
frozen real lineage in a single file:

```sh
git clone viewer/testdata/sample-lineage.bundle /tmp/sample
```

It contains every case a reader must handle — a seed, a death with a backtrace,
no-change generations, a changed generation, and out-of-loop `external edit`
commits interleaved so that anything counting positionally gets the generation
number wrong.

---

## The contract

**Your file is loaded top to bottom, once. That load is your life.**

```
emacs -Q --batch -l /work/organism.el
```

No `-f`. No entry-point convention. Definitions define; the single final
non-definition form is what actually happens.

**Only a generation that exits 0 gets to change the world.** A nonzero exit
discards every write that generation made — including files it created — and
restores `organism.el` from the last surviving commit.

That is the entire judgement the kernel makes. There is no validity check, no
pre-screening, no syntax gate. Pre-screening would prevent the organism from
ever experiencing the failure mode, which is the thing worth observing.

---

## The journal

The kernel's honest, append-only account. Exactly one entry per generation, one
lineage commit per generation.

```
## gen 7 — changed +12/-3 — 2026-08-12T14:03:22-07:00
## gen 8 — no-change — 2026-08-12T14:05:10-07:00
## gen 9 — died — 2026-08-12T14:07:41-07:00
exit 255
~~~
error("boom")
  load-with-code-conversion("/work/organism.el" ...)
  command-line-1(("-l" "/work/organism.el"))
~~~
```

Death entries carry the exit code and the last five lines of stderr — the
observation, verbatim. They must never carry interpretation. "Be careful with
top-level forms" would specify the inference the experiment exists to observe.

Note the timing this produces: a lethal edit appears as **changed** at
generation N and the **died** appears at N+1. That adjacency, plus the diffstat
pointing at the suspect edit, is the learnable pattern. It is left as pure
observation.

The journal is **controlled only by the kernel** and is read-only inside the
sandbox.
The organism has no append channel of any kind. Its self-expression lives in
its source, which it fully controls; the ledger is the kernel's account of it.
Taking away self-narration in the ledger is the deliberate constraint:
self-knowledge must come from evidence.

Generation number is the count of commits whose subject starts with `gen `, so
the journal is derivable from `git log` and out-of-loop edits cannot skew it.

---

## The model syscall

The organism does not have an API key and cannot make IP network connections.
It has one model capability, exposed through a read-only Unix socket:

```text
generate(prompt, requested output tokens) -> text or error
```

On the wire this is a `POST /generate` whose body is the raw UTF-8 prompt and
whose `X-Ouroboros-Max-Output-Tokens` header is the requested maximum. A
successful response body is raw UTF-8 model output. That is the entire contract.

For each generation, `kernel.sh` starts `model_broker.py` outside the sandbox.
The broker owns the selected credential, chooses the configured provider and
model, translates the request into that provider's protocol, and reduces the
response back to text. The credential enters the broker through a private pipe;
it is never placed in process arguments, a file, or the sandbox. The broker and
its private socket are destroyed when the generation ends.

Changing Claude to GPT, a local model, or a future backend therefore changes the
instrument, not the organism. The same lineage code sees the same syscall.

---

## Containment

Each generation runs under `bwrap`:

- `--clearenv` with only `HOME` and `PATH` restored. No provider, model, API key,
  or other host credential crosses the boundary.
- `--unshare-net`: the organism has no IP network. It does not receive DNS
  configuration or TLS roots because it has no reason to contact an API.
- `/usr` read-only. The broker's private directory is mounted read-only at
  `/kernel`, exposing only the Unix socket capability.
- Only the lineage worktree is writable, bound at `/work`.
- `journal.md` ro-bound over that writable mount: reads succeed, writes fail
  with EROFS, sibling files remain writable.
- Two clocks: `timeout` around bwrap catches slow API loops; `ulimit -t` inside
  catches spinning.
- PID, IPC and UTS namespaces unshared; `--die-with-parent`.

The wall and CPU clocks remain kernel policy rather than part of the model
syscall. Strong memory and process ceilings can be added at the same boundary
with cgroup v2 without changing organism code or the syscall contract.

---

## Running it

```sh
# Anthropic is the kernel's default backend.
export ANTHROPIC_API_KEY=...
LINEAGE=~/ouroboros-lineage GENERATIONS=5 ./kernel.sh

# Or back the same organism syscall with an OpenAI model.
export OPENAI_API_KEY=...
LINEAGE=~/ouroboros-lineage MODEL_PROVIDER=openai \
  MODEL_NAME=gpt-5.6 GENERATIONS=5 ./kernel.sh
```

| variable | default | meaning |
|---|---|---|
| `LINEAGE` | *required* | path to the lineage worktree |
| `GITDIR` | `$LINEAGE.git` | separate git dir, outside the worktree |
| `MODEL_PROVIDER` | `anthropic` | kernel backend: `anthropic` or `openai` |
| `MODEL_NAME` | provider default | kernel-side model ID |
| `MODEL_MAX_OUTPUT_TOKENS` | 12000 | maximum granted per model syscall |
| `MODEL_REQUEST_TIMEOUT` | 600 | backend request timeout in seconds |
| `MODEL_MAX_PROMPT_BYTES` | 4194304 | maximum syscall prompt size |
| `GENERATIONS` | 10 | generations this run |
| `WALL` | 600 | wallclock seconds per generation |
| `CPU` | 120 | CPU seconds per generation |

Inspecting a lineage:

```sh
G() { git --git-dir=~/ouroboros-lineage.git --work-tree=~/ouroboros-lineage "$@"; }
G log --oneline                              # the phylogeny
G diff HEAD~1 -- organism.el                 # what the last generation did
cat ~/ouroboros-lineage/journal.md           # the kernel's account
```

---

## Deliberate non-features

These are requirements, not omissions.

**No validity check.** Death-and-revert is the only judge. Pre-screening
prevents the organism from ever experiencing mortality, and would falsely
reject legitimate top-level model calls.

**No `-f`.** The load is the life. An entry-point convention belongs inside the
file, not in the kernel.

**No organism-writable append channel.** Self-expression lives in the source.

**No thrash caps or consecutive-death limits.** Prevention would itself become
the dominant structure; learning to avoid thrash is the interesting outcome.

**No silence prevention.** A generation that exits 0 without changing anything
recurs forever and looks like convergence. Unrecoverable by construction, and
allowed on purpose — record, don't rescue.

**No advice, hints or budget numbers** in the journal or the prompt.

**No corpse file.** The journal death entry is the record.

**Never merge lineage branches into main.**

---

## A note on file size

Nothing bounds how large the organism can grow — except that a reply which
outgrows `max_tokens` truncates, truncated elisp almost certainly fails to load,
and the death path absorbs it. File size is bounded by mortality rather than by
a rule.

---

## Phase 2, specified and deferred

Not implemented. Add `--unshare-net`, so the organism cannot reach the network
at all. API access becomes a file protocol: the organism writes
`/work/api/req-N.json` (write-then-rename for atomicity), the kernel polls
during the generation, forwards to the API with the key held outside the
sandbox and the model pinned, and writes `/work/api/reply-N.json`. Per-generation
and cumulative budgets are enforced in the broker; a refused call returns
structured error JSON, and the refusal itself is the only signal — no budget
disclosure in the prompt.

Resource accounting moves to a cgroup scope so the kernel can read `memory.peak`
and `cpu.stat` and append them to the journal as raw fact. Experimental design:
one lineage branch per budget level, three or four levels, compared. A single
run cannot distinguish "no structure emerged" from "budget too tight."

---

## What this does not demonstrate

It is not self-improvement — there is no objective, so "better" is undefined.
It is not autonomy — it runs when the kernel runs it. It is not novel in
mechanism; a program that rewrites itself via an oracle is old.

What is unusual is the *placement* of the prompt. Almost every system of this
shape keeps the instruction outside the artifact, in the harness, where the
artifact cannot reach it. Putting it inside is one line of design and it changes
the character of the loop: the organism is not being iterated on, it is
iterating on the conditions of its own iteration.

---

## Open questions

- What is worth preserving across generations, and what is scaffolding?
- Is editing the prompt more leveraged than editing the code?
- Does a program with a memory of its own development behave differently from
  one without?
- What would convergence look like, and would it be success or exhaustion?

---

## The primordial branch

`git checkout primordial` is the first lineage, run under the original physics
and preserved unchanged. It is a fossil, not documentation — one repository
instead of two, an `-f` entry point, a validity check, an organism-writable
note channel, and a sandbox that inherited the parent environment.

It is worth keeping because it contains **its own kernel**: the organism there
and the harness that ran it are the same commit, so it is internally consistent
in a way this branch cannot be about it. See `PRIMORDIAL.md` on that branch.
