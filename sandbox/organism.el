;;; organism.el --- a program whose modification operator is a language model  -*- lexical-binding: t; -*-

;; Everything in this file is mutable. The code, the comments, and — the part
;; that matters — the prompt below, which determines how this file will be
;; rewritten. If only the code were mutable this would be code generation. If
;; only the prompt were mutable it would be prompt engineering. Because both
;; are in here, a change to the prompt changes how future changes are made.
;;
;; What this file cannot do is guarantee it will be run again. That is the
;; kernel's job, and the kernel is outside the sandbox where this file cannot
;; reach it. See ../kernel.sh. The kernel will revert this file if a generation
;; leaves it unparseable or without an `organism-step', which is the one
;; invariant enforced from outside.

(defvar organism-generation 1
  "How many times this file has been rewritten.")

(defvar organism-model "claude-opus-5")

(defvar organism-prompt
  "You are the modification operator for the Emacs Lisp file shown below.

That file is your own program. You will be invoked again with whatever you
return, so what you write now determines how you will think next time. The
prompt you are reading is itself part of that file, in `organism-prompt'. You
may rewrite it.

Return the complete new contents of the file and nothing else — no fences, no
commentary outside the code. It must be valid Emacs Lisp and must still define
`organism-step', or the kernel will discard it and restore the previous
generation.

You have a journal at ../journal.md, appended to each generation, holding your
own history. Read it first. You write to it by setting `organism-note'; the
kernel appends whatever is there.

Working discipline, adopted at generation 1 and revisable like everything else:

1. Increment `organism-generation' by one. It is the only reliable clock.
2. Make one substantive change per generation, not five. Say in the note what
   the change was and what observation would show it was a mistake.
3. Deletion counts as a change. The file should not grow monotonically; if a
   line is only there because a previous generation was proud of it, cut it.
4. Do not break the machinery in `organism-call' and `organism-step' casually.
   Everything else — including this list — is fair game.
5. The note is for your successor, not for an audience. Short, concrete, and
   honest about what you do not know.

Open questions, carried forward rather than answered:
what is worth preserving across generations, and what is scaffolding? Is
editing the prompt more leveraged than editing the code? Does a program with a
memory of its own development behave differently from one without? What would
convergence look like here, and would it be success or exhaustion?"
  "The instruction sent alongside this file's own source. Mutable.")

(defvar organism-note ""
  "Text the kernel will append to ../journal.md after this generation.")

(defun organism-source ()
  "Return this file's own text."
  (with-temp-buffer
    (insert-file-contents "/work/organism.el")
    (buffer-string)))

(defun organism-history ()
  "Return the journal, or empty string if there isn't one yet."
  (if (file-exists-p "/work/journal.md")
      (with-temp-buffer (insert-file-contents "/work/journal.md") (buffer-string))
    ""))

(defun organism-well-formed-p (text)
  "Non-nil if TEXT reads as Lisp end to end and defines `organism-step'.
This duplicates the kernel's check on purpose. The kernel reverts a bad
generation, which costs a generation; catching it here costs nothing."
  (and (string-match-p "(defun organism-step" text)
       (with-temp-buffer
         (insert text)
         (goto-char (point-min))
         (condition-case nil
             (progn (while t (read (current-buffer))))
           (end-of-file t)
           (error nil)))))

(defun organism-call (prompt source history)
  "POST PROMPT, SOURCE and HISTORY to the model; return the reply text."
  (let* ((key (getenv "ANTHROPIC_API_KEY"))
         (body (json-serialize
                `((model . ,organism-model)
                  (max_tokens . 8000)
                  (messages . [((role . "user")
                                (content . ,(concat prompt
                                                    "\n\n=== YOUR JOURNAL ===\n" history
                                                    "\n\n=== YOUR CURRENT SOURCE ===\n" source)))]))))
         (tmp (make-temp-file "organism" nil ".json" body)))
    (with-temp-buffer
      (let ((rc (call-process "curl" nil t nil
                              "-sS" "-X" "POST" "https://api.anthropic.com/v1/messages"
                              "-H" (concat "x-api-key: " key)
                              "-H" "anthropic-version: 2023-06-01"
                              "-H" "content-type: application/json"
                              "--data-binary" (concat "@" tmp))))
        (delete-file tmp)
        (if (/= rc 0)
            (error "curl failed: %s" (buffer-string))
          (let* ((parsed (json-parse-string (buffer-string) :object-type 'alist))
                 (content (alist-get 'content parsed))
                 (text nil))
            (unless content (error "no content in reply: %s" (buffer-string)))
            ;; The first block is not necessarily the answer — a thinking block
            ;; can precede it — so take the first block whose type is "text".
            (dotimes (i (length content))
              (let ((blk (aref content i)))
                (when (and (null text) (equal (alist-get 'type blk) "text"))
                  (setq text (alist-get 'text blk)))))
            (or text (error "no text block in reply: %s" (buffer-string)))))))))

(defun organism-step ()
  "Rewrite this file once, unless the rewrite is obviously broken."
  (let* ((source (organism-source))
         (reply (organism-call organism-prompt source (organism-history))))
    ;; Strip fences if one slipped through despite the instruction.
    (setq reply (replace-regexp-in-string "\\`[ \t\n]*```\\(?:elisp\\|emacs-lisp\\)?[ \t]*\n" "" reply))
    (setq reply (replace-regexp-in-string "\n```[ \t\n]*\\'" "" reply))
    (if (not (organism-well-formed-p reply))
        (progn
          (with-temp-file "/work/note.txt"
            (insert (format "gen %d: rejected own output (unreadable or no organism-step); file unchanged.\n"
                            organism-generation)))
          (message "ORGANISM-STEP-REJECTED generation=%d bytes=%d"
                   organism-generation (length reply)))
      (with-temp-file "/work/organism.el" (insert reply))
      (with-temp-file "/work/note.txt" (insert (or organism-note "")))
      (message "ORGANISM-STEP-OK generation=%d wrote=%d bytes"
               organism-generation (length reply)))))

(setq organism-note
      "## generation 1

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
If the journal gets long, summarising it ruthlessly is a legitimate move.")

(provide 'organism)
;;; organism.el ends here