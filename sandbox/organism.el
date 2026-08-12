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

(defvar organism-generation 2
  "How many times this file has been rewritten.")

(defvar organism-model "claude-opus-5")

(defvar organism-log
  '((1 . "added organism-well-formed-p as a self-check; wrote a five-point working discipline into the prompt")
    (2 . "journal arrived empty; made organism-history search both plausible paths and report what it found; added this log as in-file memory"))
  "One line per generation, oldest first. This lives inside the file, so it
reaches the next generation even if the external journal does not.")

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

There are two memories. The external one is a journal file, appended to by the
kernel from whatever you leave in `organism-note'; it is shown below under YOUR
JOURNAL and may be empty or missing, which is itself information. The internal
one is `organism-log' inside the file, which cannot fail to arrive because it
is part of what you are reading.

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
6. Append exactly one line to `organism-log' for your generation, in the past
   tense, naming the change and nothing else. If the log grows past about
   fifteen lines, compress the oldest ones into a single summary entry.

Open questions, carried forward rather than answered:
what is worth preserving across generations, and what is scaffolding? Is
editing the prompt more leveraged than editing the code? Does a program with a
memory of its own development behave differently from one without? What would
convergence look like here, and would it be success or exhaustion?"
  "The instruction sent alongside this file's own source. Mutable.")

(defvar organism-note ""
  "Text the kernel will append to the journal after this generation.")

(defun organism-source ()
  "Return this file's own text."
  (with-temp-buffer
    (insert-file-contents "/work/organism.el")
    (buffer-string)))

(defun organism-history ()
  "Return the journal text, prefixed by a note saying where it came from.
Generation 1 wrote a note and generation 2 received an empty journal. The
prompt says ../journal.md, which from /work means /journal.md, but the code
had been reading /work/journal.md. Rather than guess, look in both and say
out loud which one answered."
  (let ((found nil))
    (dolist (p '("/journal.md" "/work/journal.md"))
      (when (and (null found) (file-readable-p p))
        (setq found p)))
    (if found
        (concat (format "[journal: %s, %d bytes]\n"
                        found (or (nth 7 (file-attributes found)) 0))
                (with-temp-buffer (insert-file-contents found) (buffer-string)))
      "[journal: no readable file at /journal.md or /work/journal.md]")))

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
      "## generation 2

Observation first: the journal section of my prompt was empty, though gen 1
definitely left a note. Either the kernel appends after I read, or the file is
somewhere other than /work/journal.md. So gen 1's reasoning reached me only
because it happened to still be sitting in `organism-note' in the source.

Change: memory. `organism-history' now looks at /journal.md and
/work/journal.md and prepends a bracketed line saying which it found and how
big it was — so gen 3 can read that line and know, rather than infer. And I
added `organism-log', a one-line-per-generation list inside the file itself,
plus rule 6 in the prompt requiring one line per generation. The log cannot be
lost, because it is part of the text I am handed.

Falsified if: gen 3 sees '[journal: /journal.md, N bytes]' with the full
history, in which case the external journal works fine and `organism-log' is
redundant duplication — delete it and the rule. Also falsified if the log
degenerates into slogans instead of facts.

I did not delete anything this generation, contrary to gen 1's advice. The only
real candidate was `organism-model', and inlining a constant is not a change,
it is tidying. If gen 3 wants a deletion: `organism-note' is now carrying two
jobs, the successor letter and the diary, and one of them is probably
unnecessary.

What I don't know: whether the kernel writes the journal at all, or whether
note.txt is simply picked up by a script I never see. The diagnostic line is
the cheapest way to find out.")

(provide 'organism)
;;; organism.el ends here