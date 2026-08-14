# ouroboros

A Git-recorded mutable filesystem whose fixed boot program is Emacs Lisp, whose
modification operator is a language model, and whose prompt is a variable inside
itself.

This repository is the **instrument**. The lineages it produces live as separate
branches of the `ouroboros-lineage` repository because the instrument and its
experimental records occupy different layers.

---

## What this is

This repository owns the canonical initial `organism.el`. `init-lineage.sh`
copies that exact file into every new lineage as its root seed. During a
generation, the kernel loads the lineage's copy once, but the organism can read
and write its whole speculative worktree except for kernel-owned files. A
successful generation commits the resulting filesystem state, not merely
`organism.el`.

The published organism sends its own source through a kernel model syscall,
receives a complete replacement, and writes that replacement over itself. The
instruction it sends — `organism-prompt` — is a `defvar` in the same file. So a
generation can rewrite the code, rewrite the thing that determines how the code
will be rewritten, create other state, or do all three.

`kernel.sh` runs it and mediates its capabilities. The kernel is deliberately
outside, deliberately dumb, and deliberately not Lisp.

The design goal is **emergence under constraint**: the kernel exposes a small,
stable interface and never contributes cognitive content of its own. No
cognitive instructions or advice, and no provider-specific authority, enters the
organism.
Within that capability boundary, a lineage chooses its own policies. The
instrument neither endorses nor forbids organism-side validation, memory,
archives, self-description, silence, or abandonment of reproduction.

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

## Two repositories, three kinds of branch

```
ouroboros/                  # this repo — the instrument, outside the generation loop
  organism.el               # canonical initial organism for every new lineage
  init-lineage.sh           # creates the separate repo and copies the seed
  kernel.sh
  model_broker.py
  runtime.py
  sandbox_runner.sh
  README.md
  analysis/                 # run notes (gitignored by default)

ouroboros-lineage/          # a lineage — the generation-loop record, separate repo
  organism.el               # fixed boot path; contents belong to the lineage
  journal.md                # kernel-owned; exposed read-only at /kernel/journal.md
  .ouroboros-lineage.json   # root seed plus exact instrument commit/fingerprint
  ...                       # any other lineage-created files and directories

ouroboros-lineage.git/      # separate git dir, OUTSIDE the worktree

ouroboros-lineage observations/  # trusted local observer repo, never mounted
  metadata.json                  # exact instrument commit/ref/repository
  generations/0001.json          # resource, outcome, and model-call record
```

Interleaving the two histories is what broke the original generation counter:
out-of-loop commits and generation commits were being counted together. They
record different layers of the system and belong apart.

The lineage's Git object database, index, and refs live outside the worktree via
`git init --separate-git-dir`. Git leaves a small `.git` pointer file in the host
worktree, but neither that pointer nor the host worktree is mounted into the
sandbox. `/work` is instead a disposable, size-limited filesystem populated from
the committed tree. Git authority is therefore absent rather than merely
read-only.

The lineage state is the **entire committed worktree**. `organism.el` is special
only because the kernel always loads that path to begin the next life and, after
a death, rolls that file back far enough to avoid immediately loading the same
lethal body again. Files such as `state.el`, archives, and rejected replies are
lineage conventions rather than kernel-defined slots.

The remote lineage repository has a deliberately thin topology:

- `main` contains only its viewer-facing README.
- `lineage-*` branches contain hereditary filesystem histories.
- `observations/lineage-*` branches contain trusted observer records for the
  corresponding lineage and are never mounted into a generation.
- `lineage-original` preserves the historical lineage that formerly occupied
  `main`.

Lineages are results, not code. Lineage and observation branches are never
merged into `main` or into each other.

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

The seed commit and its observation branch both record the exact Git commit,
repository/ref provenance, seed hash, and a content fingerprint of the trusted
runtime files. Before every extension, `kernel.sh` checks both records against
the current checkout. Either mismatch is a preflight failure: extending a
lineage first requires checking out its exact instrument commit. Changing the
instrument means starting a new lineage, never silently continuing an existing
one under different physics.

---

## The contract

**`organism.el` is loaded top to bottom, once. That load is one life.**

```
emacs -Q --batch -l /work/organism.el
```

No `-f`. The kernel defines the boot *path* but no function-level entry-point
convention. Whatever top-level forms the lineage leaves in `organism.el` decide
what actually happens.

**Only a generation that exits 0 publishes its speculative filesystem writes.**
The organism never writes the host lineage worktree. The kernel expands the
committed tree into a bounded tmpfs at `/work`, runs the life there, and receives
a bounded result archive only after a zero exit. Trusted host code validates
that archive before replacing and committing the lineage worktree. On a nonzero
exit, the tmpfs disappears and none of its files can survive. Rolling the boot
body back after a death, appending the journal, and making the death commit are
separate kernel recording/recovery actions, not publication of speculative
content.

That is the entire judgement the **kernel** makes. It has no validity check,
pre-screening, or syntax gate. A lineage is free to construct, retain, remove,
or ignore its own checks; doing so is behavior inside the experiment, not a
policy imposed or condoned by the instrument.

---

## The journal

The kernel's honest, append-only account. Exactly one entry per generation, one
lineage commit per generation.

```
## gen 7 — changed — organism +12/-3; 3 paths — 2026-08-12T14:03:22-07:00
## gen 8 — changed — organism unchanged; 2 other paths — 2026-08-12T14:04:10-07:00
## gen 9 — no-change — 2026-08-12T14:05:10-07:00
## gen 10 — died — 2026-08-12T14:07:41-07:00
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

The lethal `organism.el` is therefore recoverable: it is the body in the parent
of the death commit (`git show <death>^:organism.el`), and the lineage reader
exposes it directly with the matching journal observation.

Changed headings now say explicitly whether `organism.el` changed. If it did,
the entry reports its `+A/-D` line count and the total number of changed paths.
If only other files changed, the entry says `organism unchanged` rather than the
misleading `+0/-0`. The commit's full tree remains authoritative.

The journal is **controlled only by the kernel** and is read-only inside the
sandbox. The organism has no append channel into that ledger. Its
self-expression may live anywhere in the writable speculative tree; the journal
is the kernel's account of it. Taking away self-narration in the ledger is the
deliberate constraint: self-knowledge must come from evidence.

Generation number is the count of commits whose subject starts with `gen `, so
the journal is derivable from `git log` and out-of-loop edits cannot skew it.

---

## Trusted observations

The journal remains the small causal signal available to future organisms. A
separate trusted observer records what a researcher needs without feeding it
back into the loop. For every generation,
`observations/<lineage>/generations/NNNN.json` records:

- the corresponding lineage commit and exact instrument identity;
- start time, duration, exit status, systemd result, aggregate CPU time, peak
  cgroup memory, and OOM-kill count;
- result-archive and published filesystem sizes;
- every broker request's full prompt and response, hashes, token request and
  usage, duration, stop/status metadata, and reserved/accounted spending.

This is intentionally low-distortion accounting, not syscall tracing. It
records the trusted model boundary and the generation/cgroup outcome, but does
not intercept every file operation, process creation, or libc call. The local
observation Git repository is never mounted at `/work` or `/kernel`; only its
published remote branch is available to viewers. Consequently the organism
cannot read these observations during its life unless a human deliberately
copies some observation into a later lineage commit.

---

## The model syscall

The organism does not have an API key and cannot make IP network connections.
The kernel makes one model capability available through a read-only directory
containing a Unix socket and a provider-neutral manifest:

```text
/kernel/capabilities.json                  # discoverable ABI description
/kernel/model.sock                         # capability endpoint
/kernel/journal.md                         # kernel ledger, read-only

generate(prompt, requested output tokens) -> text or error
```

On the wire this is a `POST /generate` whose body is the raw UTF-8 prompt and
whose `X-Ouroboros-Max-Output-Tokens` header is the requested maximum. A
successful response body is raw UTF-8 model output. The same manifest describes
the effective wall, aggregate CPU, memory, task, filesystem, prompt,
output-token, request-timeout, spending, network, and publication constraints.
Constraint disclosure is canonical rather than an experimental mode: every
organism gets the physics it is actually running under.

A Unix socket is not self-describing. The filesystem can reveal that
`model.sock` is a socket, but not whether its bytes speak HTTP, what paths exist,
or what request shape they require. A `help` endpoint would still require prior
knowledge that the protocol is HTTP. The adjacent manifest supplies that
bootstrap information as inert data: socket path, transport, method, route,
headers, body, and success response. It contains no provider, model, credential,
advice, or generation-specific content.

The canonical seed in this repository reads that manifest, selects its
`generate` and `journal` entries, and includes the complete manifest in its model
request. That is how a fresh organism knows the model operation and its resource
physics exist: the discovery mechanism starts in the instrument seed, while the
manifest supplies the current provider-neutral contract. If a lineage later
overwrites its inspection or reproduction code, the kernel does not patch it
back.

`init-lineage.sh` is the only supported creation path. It copies the canonical
seed, creates an empty journal, records the seed plus exact instrument identity,
makes the root commit, and initializes the corresponding trusted observation
branch. An existing lineage is never silently upgraded to a new kernel ABI.

For each generation, `kernel.sh` starts `model_broker.py` outside the sandbox.
The broker owns the selected credential, chooses the configured provider and
model, translates the request into that provider's protocol, and reduces the
response back to text. The credential enters the broker through a private pipe;
it is never placed in process arguments, a file, or the sandbox. The broker and
its private socket are destroyed when the generation ends.

The same broker owns a generation-local spending ledger. Before every provider
request it reserves the conservative maximum cost of the prompt plus the
requested output limit. A request that cannot fit is rejected before reaching
the provider. When a successful provider response contains usage counts, unused
reservation is returned to the generation ledger; missing or failed usage data
receives no refund. The dollar ceiling is therefore hard with respect to the
configured price schedule. Recognized model prices have explicit defaults;
unrecognized models require explicit input and output prices instead of silently
guessing. The checked-in defaults should be updated when provider prices change.

As of August 2026, the OpenAI defaults follow the
[official GPT-5.6 prices](https://openai.com/api/pricing/) and the Anthropic
default follows the [official Claude Opus 4.8 price](https://www.anthropic.com/news/claude-opus-4-8).
The prompt-byte limit plus a conservative framing allowance keeps the supported
requests below premium long-context pricing thresholds.

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
  `/kernel`, exposing the socket, manifest, journal, committed input archive,
  and trusted staging runner.
- No host worktree path is writable or even mounted into the organism.
  `/work` is a 256 MiB tmpfs by default; `/tmp` and `/run` are separately bounded
  disposable tmpfs mounts.
- The kernel journal is available at `/kernel/journal.md` and cannot be replaced
  through the speculative worktree.
- A transient user service bounds wall time with `RuntimeMaxSec`. Its cgroup
  `CPUQuota`, derived from requested aggregate CPU seconds divided by wall time,
  bounds total CPU use across the complete process tree rather than applying a
  misleading per-process `ulimit`.
- The same transient cgroup surrounds the complete sandbox process tree.
  `MemoryMax=1G`, `MemorySwapMax=0`, and `TasksMax=64` are the default hard
  ceilings; the broker is trusted instrument code and remains outside that
  scope.
- PID, IPC and UTS namespaces unshared; `--die-with-parent`.

Wall time, aggregate CPU, memory, task count, filesystem sizes, published file
count, and model spending remain trusted kernel policy rather than organism
conventions. Their effective values are disclosed in the capability manifest.
A working user systemd manager is required; there is no unbounded portability
bypass.

---

## Running it

```sh
# Create a new lineage and observer from this checkout's exact commit.
LINEAGE=~/lineage-gpt-03 LINEAGE_BRANCH=lineage-gpt-03 \
  ./init-lineage.sh

# Anthropic is the kernel's default backend.
export ANTHROPIC_API_KEY=...
LINEAGE=~/lineage-gpt-03 GENERATIONS=5 ./kernel.sh

# Or back the same organism syscall with an OpenAI model.
export OPENAI_API_KEY=...
LINEAGE=~/lineage-gpt-03 MODEL_PROVIDER=openai \
  MODEL_NAME=gpt-5.6 GENERATIONS=5 ./kernel.sh
```

| variable | default | meaning |
|---|---|---|
| `LINEAGE` | *required* | path to the lineage worktree |
| `GITDIR` | `$LINEAGE.git` | separate git dir, outside the worktree |
| `LINEAGE_BRANCH` | basename of `LINEAGE` | lineage branch recorded at initialization |
| `OBSERVATION` | `$LINEAGE.observations` | trusted local observation repository |
| `OBSERVATION_BRANCH` | `observations/$LINEAGE_BRANCH` | corresponding remote branch name |
| `MODEL_PROVIDER` | `anthropic` | kernel backend: `anthropic` or `openai` |
| `MODEL_NAME` | provider default | kernel-side model ID |
| `MODEL_MAX_OUTPUT_TOKENS` | 12000 | maximum granted per model syscall |
| `MODEL_REQUEST_TIMEOUT` | 600 | backend request timeout in seconds |
| `MODEL_MAX_PROMPT_BYTES` | 196608 | maximum syscall prompt size |
| `MODEL_BUDGET_USD` | 1.00 | hard provider-spending budget per generation |
| `MODEL_INPUT_USD_PER_MTOK` | recognized-model price | conservative uncached input price |
| `MODEL_OUTPUT_USD_PER_MTOK` | recognized-model price | output price |
| `GENERATIONS` | 10 | generations this run |
| `WALL` | 600 | wallclock seconds per generation |
| `CPU_BUDGET_SECONDS` | 120 | aggregate sandbox CPU seconds per generation |
| `MEMORY_MAX` | 1G | cgroup hard memory ceiling for the sandbox process tree |
| `MEMORY_SWAP_MAX` | 0 | additional swap allowed to the sandbox process tree |
| `TASKS_MAX` | 64 | cgroup ceiling for sandbox processes and threads |
| `WORKTREE_MAX_BYTES` | 268435456 | hard size of speculative `/work` tmpfs |
| `WORKTREE_MAX_FILES` | 10000 | maximum Git-representable files published |
| `TMP_MAX_BYTES` | 67108864 | hard size of `/tmp` |
| `RUN_MAX_BYTES` | 16777216 | hard size of `/run` and organism log |

Inspecting a lineage:

```sh
G() { git --git-dir=~/ouroboros-lineage.git --work-tree=~/ouroboros-lineage "$@"; }
G log --oneline                              # the phylogeny
G diff HEAD~1 -- organism.el                 # what the last generation did
cat ~/ouroboros-lineage/journal.md           # the kernel's account
git -C ~/ouroboros-lineage.observations log  # trusted observer history
```

## Lineage reader and optional viewer

`viewer/lineage_reader.py` is the reusable, read-only interpretation layer. It
understands generation commits, out-of-loop context, journal correspondence,
state and rejected artifacts, the committed boot source behind a death, and
arbitrary generation comparisons without requiring a web interface:

```sh
git clone viewer/testdata/sample-lineage.bundle /tmp/sample
python3 viewer/lineage_reader.py /tmp/sample summary
python3 viewer/lineage_reader.py /tmp/sample generation 5
python3 viewer/lineage_reader.py /tmp/sample compare 1 5
python3 viewer/lineage_reader.py /tmp/sample verify
```

`viewer/server.py` is an optional browser presentation of the same reader:

```sh
python3 viewer/server.py /tmp/sample
```

---

## Deliberate non-features

These are requirements, not omissions.

**No kernel validity check.** Death-and-revert is the instrument's only judge.
The lineage may implement any organism-side checks it chooses, and may later
remove them; the instrument is neutral toward that behavior.

**No `-f`.** The load is the life. An entry-point convention belongs inside the
file, not in the kernel.

**No organism-writable journal channel.** The lineage may express and remember
through any writable worktree path, but cannot rewrite the kernel's ledger.

**No thrash caps or consecutive-death limits.** Prevention would itself become
the dominant structure; learning to avoid thrash is the interesting outcome.

**No silence prevention.** A generation that exits 0 without changing anything
recurs forever and looks like convergence. The kernel treats this as a valid
outcome and does not rescue it — record, don't prescribe.

**No advice or hints in the journal.** The capability manifest does disclose
the enforced resource and spending limits, because a canonical contract lets
the organism reason about its actual physics rather than waste generations
guessing values the kernel already knows.

**Never merge lineage branches into main.**

---

## Storage and publication

The speculative filesystem is physically bounded while the generation runs,
not merely checked after the fact. A trusted result decoder also limits the
published regular-file bytes and file count, rejects Git control paths, rejects
kernel-owned paths, and accepts only directories, regular files, and symlinks.
Every published result is force-added so `.gitignore` cannot create invisible
hereditary state.

---

## Current boundary and remaining hardening choices

Network isolation, the kernel-mediated model socket, provider separation,
credential isolation, spending reservation, disposable publication, bounded
filesystems, cgroup CPU/memory/task ceilings, exact instrument-commit pinning,
and trusted observation branches are implemented. The instrument does not
impose a custom seccomp syscall allowlist or expensive all-syscall tracing. A
seccomp allowlist is especially easy to make accidentally cognitive or brittle because
Emacs, libc, the dynamic loader, and `curl` require a broad syscall surface; it
should be derived from observed execution and tested across supported hosts
rather than guessed.

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
