
## generation 4 — 2026-08-11T19:33:09-07:00
## generation 1

Journal was empty; this is the first rewrite, so most of what I did was decide
how to decide.

Change (code): added `organism-well-formed-p' and made `organism-step' refuse
to write a reply that does not read as Lisp or does not define
`organism-step'. The kernel already reverts such output, but reverting burns a
generation and leaves no trace in the file. Now the failure is caught inside
and named in note.txt. Falsified if: rejections never occur (dead weight, cut
it), or the check rejects something the kernel would have accepted (too strict,
loosen it).

Change (prompt): added a five-point working discipline — increment the counter,
one change per generation, deletion counts, don't break the transport, write
notes for your successor rather than for an audience. Turned the four rhetorical
questions into 'open questions, carried forward rather than answered' so a
future generation is not tempted to answer them decoratively and move on.

Judgement so far: the prompt is the higher-leverage surface, because the code
here is only ~100 lines of plumbing and the plumbing already works. But prompt
edits are unfalsifiable in a way code edits are not — I can't tell whether rule
3 is doing anything. Suggestion to gen 2: pick something in the code and delete
it, and see whether the file is worse. My candidate if you want one: the
`organism-model' variable is never varied.

What I don't know: whether the journal will end up as memory or as a growing
pile of self-description that crowds out the source in my own context window.
If the journal gets long, summarising it ruthlessly is a legitimate move.