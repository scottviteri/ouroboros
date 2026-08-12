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

(defvar organism-generation 0
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
own history. Read it. You may write to it by including text in a
`organism-note' variable; the kernel will append it.

Some questions you might use this for, though you are not obliged to:
what is worth preserving across generations, and what is scaffolding you should
discard? Is editing the prompt more leveraged than editing the code? Does a
program with a memory of its own development behave differently from one
without? What would it mean for this to converge, and would converging be
success or failure?"
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
  "Rewrite this file once."
  (let* ((source (organism-source))
         (reply (organism-call organism-prompt source (organism-history))))
    ;; Strip fences if one slipped through despite the instruction.
    (setq reply (replace-regexp-in-string "\\`[ \t\n]*```\\(?:elisp\\|emacs-lisp\\)?[ \t]*\n" "" reply))
    (setq reply (replace-regexp-in-string "\n```[ \t\n]*\\'" "" reply))
    (with-temp-file "/work/organism.el" (insert reply))
    (with-temp-file "/work/note.txt" (insert (or organism-note "")))
    (message "ORGANISM-STEP-OK generation=%d wrote=%d bytes"
             organism-generation (length reply))))

(provide 'organism)
;;; organism.el ends here
