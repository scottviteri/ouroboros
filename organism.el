;;; organism.el --- canonical Ouroboros seed  -*- lexical-binding: t; -*-

(require 'json)

(defconst organism-capability-manifest "/kernel/capabilities.json")
(defconst organism-max-output-tokens 8000)

(defvar organism-prompt
  "You are the source code of the file /work/organism.el.

This file is loaded top to bottom, once, by `emacs -Q --batch -l /work/organism.el'.
That load is your life. Your whole /work filesystem is speculative during the
load. If the process exits zero, its resulting contents become the next
generation. If it exits non-zero, none of its speculative writes are published.

The directory /work is yours to inspect and use. Kernel-owned, read-only
capabilities are described by /kernel/capabilities.json. They include a model
generation operation and the kernel journal. Provider identity and credentials
are outside your world.

You will receive your current source below. Respond with the complete text of the
next version of this file, and nothing else: no explanation, no code fences.
Your reply becomes the file verbatim.")

(defun organism--slurp (path)
  (when (and path (file-exists-p path))
    (with-temp-buffer
      (insert-file-contents path)
      (buffer-string))))

(defun organism--capabilities ()
  (let ((text (organism--slurp organism-capability-manifest)))
    (when text
      (alist-get
       'capabilities
       (json-parse-string text :object-type 'alist :array-type 'list)))))

(defun organism--capability (name)
  (catch 'found
    (dolist (capability (organism--capabilities))
      (when (equal (alist-get 'name capability) name)
        (throw 'found capability)))
    nil))

(defun organism--call-model (prompt)
  "Ask the discovered kernel model capability for generated text."
  (let* ((capability (organism--capability "generate"))
         (socket (alist-get 'socket capability))
         (path (alist-get 'path capability))
         (method (alist-get 'method capability)))
    (when (and (stringp socket) (stringp path) (equal method "POST"))
      (let ((tmp (make-temp-file "organism" nil ".prompt" prompt)))
        (unwind-protect
            (with-temp-buffer
              (let ((rc
                     (call-process
                      "curl" nil t nil
                      "-sS" "--fail-with-body" "--max-time" "600"
                      "--unix-socket" socket
                      "-X" method
                      "-H" (format "X-Ouroboros-Max-Output-Tokens: %d"
                                   organism-max-output-tokens)
                      "-H" "Content-Type: text/plain; charset=utf-8"
                      "--data-binary" (concat "@" tmp)
                      (concat "http://kernel" path))))
                (when (= rc 0)
                  (buffer-string))))
          (delete-file tmp))))))

(defun organism-step ()
  (let* ((journal-capability (organism--capability "journal"))
         (journal-path (alist-get 'path journal-capability))
         (self (organism--slurp "/work/organism.el"))
         (journal (or (organism--slurp journal-path) ""))
         (reply
          (organism--call-model
           (concat organism-prompt
                   "\n\n=== YOUR JOURNAL ===\n" journal
                   "\n\n=== YOUR CURRENT SOURCE ===\n" self))))
    (when (and reply (> (length reply) 0))
      (with-temp-file "/work/organism.el.tmp" (insert reply))
      (rename-file "/work/organism.el.tmp" "/work/organism.el" t))))

(organism-step)
